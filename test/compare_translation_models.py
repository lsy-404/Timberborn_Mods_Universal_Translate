"""Compare localization models with repository samples and reported API usage."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import statistics
import sys
import time
from typing import List, Optional, Tuple

import requests
import tiktoken
import toml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
import translate_mods
from translation_prompt import restore_source_line_breaks

LANGUAGES = ('zhCN', 'zhTW', 'jaJP', 'deDE')
VARIANTS = {
    'original': {'model': 'gpt-4o-mini', 'input': 0.15, 'cached': 0.075, 'write': 0.15, 'output': 0.60},
    'luna_none': {'model': 'gpt-6-luna', 'input': 0.10, 'cached': 0.01, 'write': 0.125, 'output': 0.50},
    'luna_background': {'model': 'gpt-6-luna', 'input': 0.10, 'cached': 0.01, 'write': 0.125, 'output': 0.50},
}
ENCODING = tiktoken.get_encoding('o200k_base')
PLACEHOLDER = re.compile(r'\{[^{}\n]+\}')
TAG = re.compile(r'</?[A-Za-z][^>\n]*>')


def original_prompt(key: str, new_text: str, mod_name: str, target_language: str, raw: Optional[str]=None, current_translation: Optional[str]=None, prompt: Optional[str]=None, specific_prompt: Optional[str]=None, glossary_hints: Optional[List[str]]=None) -> Tuple[str, str]:
    """
    Build system and user prompts for translation

    Args:
        glossary_hints: Optional list of glossary hints to include in prompt

    Returns:
        Tuple of (system_prompt, user_prompt)
    """
    lang_name = translate_mods.LANGUAGE_NAMES.get(target_language, target_language)
    system_prompt = f'You are a professional game localization translator specializing in the game "Timberborn" and its mod "{mod_name}". Task: Translate the given text into {lang_name}Output rules (STRICT) Output ONLY the translated text. Do NOT add explanations, comments, notes, quotes, keep original formatting. Do NOT repeat the source text. Do NOT add prefixes such as "Translation:", "Result:", or similar. If the input is empty, output an empty string.'
    prompt_parts = [f'Key name: {key}']
    if new_text:
        prompt_parts.append(f'New Text to Translate: "{new_text}"')
    if raw:
        prompt_parts.append(f'Original Text (Old): "{raw}"')
    if current_translation:
        prompt_parts.append(f'Current Translation: "{current_translation}"')
    if prompt:
        prompt_parts.append(f'Field Hint: {prompt}')
    if specific_prompt:
        prompt_parts.append(f'Specific Note: {specific_prompt}')
    if glossary_hints:
        for hint in glossary_hints:
            prompt_parts.append(f'Glossary Reference: {hint}')
    user_prompt = ' - '.join(prompt_parts)
    return (system_prompt, user_prompt)


def prepare_cases(sample_limit=12):
    config = toml.load(ROOT / '.github/config/config.toml')
    translate_mods.LANGUAGE_NAMES = config['languages']['locale_names']
    glossary = translate_mods.load_glossary(str(ROOT / 'data/_glossary.toml'))
    samples = json.loads((ROOT / 'test/translation_model_samples.json').read_text())[:sample_limit]
    cases = []
    for sample in samples:
        data = toml.load(ROOT / 'data' / sample['file'])
        entry = data[sample['key']]
        meta = data.get('_meta', {})
        mod_name = meta.get('name', Path(sample['file']).stem)
        merged = translate_mods.merge_glossaries(glossary, meta.get('glossary', {}))
        for language in LANGUAGES:
            source = sample.get('new_text', entry['raw'])
            hints = []
            if 'new_text' in sample:
                source, hints = translate_mods.generate_glossary_hints(
                    source, language, merged, config['languages']['supported']
                )
            system, user = original_prompt(
                key=sample['key'], new_text=source, mod_name=mod_name,
                target_language=language,
                raw=entry['raw'] if 'new_text' in sample else None,
                current_translation=entry.get(language) if 'new_text' in sample else None,
                prompt=meta.get('prompt'), specific_prompt=entry.get('prompt'),
                glossary_hints=hints,
            )
            cases.append({
                **sample, 'language': language, 'mod_name': mod_name,
                'source': source, 'reference': entry.get(language, ''),
                'system_prompt': system, 'user_prompt': user,
            })
    return cases, config, glossary


def language_context(language, config, glossary):
    terms = {}
    for term, values in glossary.items():
        localized = values.get('translations', values).get(language)
        if localized:
            terms[term] = localized
    return (
        f"Target language: {config['languages']['locale_names'][language]}. "
        'Use the written script and conventions of this locale. '
        'Translate only the source requested in the user message.\n'
        'Established terminology: ' + json.dumps(terms, ensure_ascii=False, sort_keys=True)
    )


def build_request(variant, case, background, localized_context):
    if variant == 'luna_background':
        content = [
            {'type': 'text', 'text': text, 'prompt_cache_breakpoint': {'mode': 'explicit'}}
            for text in (background, localized_context)
        ]
        user = f"Mod: {case['mod_name']}\n{case['user_prompt']}"
    else:
        content = case['system_prompt']
        user = case['user_prompt']
    body = {
        'model': VARIANTS[variant]['model'], 'service_tier': 'default',
        'max_completion_tokens': 800,
        'messages': [{'role': 'system', 'content': content}, {'role': 'user', 'content': user}],
    }
    if variant != 'original':
        body['reasoning_effort'] = 'none'
    if variant == 'luna_background':
        body['prompt_cache_options'] = {'mode': 'explicit', 'ttl': '30m'}
    return body


def parse_usage(usage, prices):
    input_tokens = usage['prompt_tokens']
    output_tokens = usage['completion_tokens']
    details = usage.get('prompt_tokens_details', {})
    cached = details.get('cached_tokens', 0)
    writes = details.get('cache_write_tokens', 0)
    ordinary = input_tokens - cached - writes
    if min(ordinary, cached, writes, output_tokens) < 0:
        raise ValueError('Inconsistent token usage returned by API')
    input_cost = (ordinary * prices['input'] + cached * prices['cached'] + writes * prices['write']) / 1e6
    output_cost = output_tokens * prices['output'] / 1e6
    return {
        'input_tokens': input_tokens, 'output_tokens': output_tokens,
        'ordinary_input_tokens': ordinary, 'cached_tokens': cached, 'cache_write_tokens': writes,
        'cache_write_reported': 'cache_write_tokens' in details,
        'reasoning_tokens': usage.get('completion_tokens_details', {}).get('reasoning_tokens'),
        'input_cost_usd': input_cost, 'output_cost_usd': output_cost,
        'cost_usd': input_cost + output_cost,
        'input_cost_without_cache_usd': input_tokens * prices['input'] / 1e6,
    }


def numbers(text):
    text = TAG.sub('', PLACEHOLDER.sub('', text)).replace('％', '%')
    text = re.sub(r'(\d)\s+%', r'\1%', text)
    text = re.sub(r'(?<=\d)[ \u00a0\u202f](?=\d{3}(?:\D|$))', '', text)
    found = re.findall(r'[+-]?\d+(?:[.,]\d+)*%?', text)
    normalized = []
    for value in found:
        if re.fullmatch(r'\d{1,3}(?:[,.]\d{3})+%?', value):
            value = value.replace(',', '').replace('.', '')
        else:
            value = value.replace(',', '.')
        normalized.append(value)
    return Counter(normalized)


def format_issues(source, output):
    issues = []
    if not output.strip():
        issues.append('empty_output')
    if Counter(PLACEHOLDER.findall(source)) != Counter(PLACEHOLDER.findall(output)):
        issues.append('placeholder_changed')
    if TAG.findall(source) != TAG.findall(output):
        issues.append('markup_changed')
    if source.count('\n') != output.count('\n') or source.count('\\n') != output.count('\\n'):
        issues.append('line_breaks_changed')
    expected, actual = numbers(source), numbers(output)
    added = actual - expected
    word_numbers = {'0': 'zero', '1': 'one|single|first', '2': 'two|both|twice|second',
                    '3': 'three|third', '4': 'four|fourth', '5': 'five|fifth',
                    '6': 'six|sixth', '7': 'seven|seventh', '8': 'eight|eighth',
                    '9': 'nine|ninth', '10': 'ten|tenth'}
    for value, words in word_numbers.items():
        allowance = len(re.findall(r'\b(?:' + words + r')\b', source, re.IGNORECASE))
        if value == '1':
            allowance += len(re.findall(r'\bper (?:hour|day|minute|second)\b', source, re.IGNORECASE))
        if added.get(value, 0) <= allowance:
            added.pop(value, None)
    if expected - actual or added:
        issues.append('numbers_changed')
    quote_pairs = (('"', '"'), ("'", "'"), ('“', '”'), ('‘', '’'),
                   ('「', '」'), ('『', '』'), ('«', '»'), ('„', '“'))
    source_quoted = any(source.startswith(a) and source.endswith(b) for a, b in quote_pairs)
    if not source_quoted and any(output.startswith(a) and output.endswith(b) for a, b in quote_pairs):
        issues.append('added_outer_quotes')
    if re.match(r'(?i)^(translation|result|translated text)\s*:', output):
        issues.append('added_prefix')
    if output.strip() == source.strip():
        issues.append('unchanged_source')
    return issues



def aggregate(records, variants=None):
    variants = VARIANTS if variants is None else variants
    summaries = {}
    for variant in variants:
        rows = [r for r in records if r['variant'] == variant]
        billed = [r for r in rows if 'usage' in r]
        completed = [r for r in billed if 'text' in r]
        metrics = {
            key: sum(r['usage'][key] for r in billed)
            for key in ('input_tokens', 'output_tokens', 'cached_tokens', 'cache_write_tokens',
                        'input_cost_usd', 'output_cost_usd', 'cost_usd', 'input_cost_without_cache_usd')
        }
        reasoning = [r['usage']['reasoning_tokens'] for r in billed]
        metrics.update(
            requests=len(rows), completed=len(completed),
            format_pass=sum(not r['format_issues'] for r in completed),
            reasoning_tokens=sum(reasoning) if reasoning and all(n is not None for n in reasoning) else None,
            cache_hit_rate=metrics['cached_tokens'] / max(1, metrics['input_tokens']),
            p50_latency_seconds=statistics.median(r['latency_seconds'] for r in completed) if completed else None,
            cache_write_reporting_complete=bool(billed) and all(r['usage']['cache_write_reported'] for r in billed),
        )
        summaries[variant] = metrics
    return summaries


def write_reports(directory, records, metadata, variants=None):
    directory.mkdir(parents=True, exist_ok=True)
    summaries = aggregate(records, variants)
    report = {'metadata': metadata, 'summaries': summaries, 'records': records}
    (directory / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    lines = [
        '# Translation model comparison', '',
        '| Variant | Completed / requests | Output checks | Input | Cache reads | Cache writes | Output | Reasoning | Cost USD | Median seconds |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
    ]
    for variant, s in summaries.items():
        latency = f"{s['p50_latency_seconds']:.2f}" if s['p50_latency_seconds'] is not None else '—'
        reasoning = s['reasoning_tokens'] if s['reasoning_tokens'] is not None else 'not reported'
        lines.append(f"| {variant} | {s['completed']}/{s['requests']} | {s['format_pass']}/{s['completed']} | {s['input_tokens']} | {s['cached_tokens']} | {s['cache_write_tokens']} | {s['output_tokens']} | {reasoning} | {s['cost_usd']:.6f} | {latency} |")
    lines.extend([
        '', f"Shared background: approximately {metadata['background_tokens_estimate']} visible tokens (o200k_base).",
        'The first cache write and every failed or uncached request remain part of the experiment.',
        'Output checks cover placeholders, tags, line breaks, numbers and added quotes; they do not establish translation accuracy.',
        'An unchanged source is flagged for review; proper names can legitimately remain unchanged.',
        'Existing repository translations are comparison references, not certified answers.',
        'Costs use reported usage and Standard API rates. Missing cache-write fields make the Luna estimate incomplete.',
    ])
    if metadata.get('replay_run_id'):
        lines.extend(['', f"Format checks were recomputed locally from the unchanged outputs of Actions run {metadata['replay_run_id']}. No additional API requests were made."])
    for variant, s in summaries.items():
        lines.append(f"- {variant}: token cache-hit rate {s['cache_hit_rate']:.1%}; input cost saved versus the same uncached prompt ${s['input_cost_without_cache_usd'] - s['input_cost_usd']:.6f}.")
    for record in records:
        if 'error' in record:
            lines.append(f"- Failed: {record['variant']}/{record['id']}/{record['language']}: {record['error']}")
    (directory / 'summary.md').write_text('\n'.join(lines) + '\n')
    cards = []
    groups = {}
    for row in records:
        groups.setdefault((row['id'], row['language']), []).append(row)
    for (case_id, language), rows in groups.items():
        first = rows[0]
        parts = [f'<article><h2>{html.escape(case_id)} · {language}</h2>',
                 '<h3>Source</h3><pre>' + html.escape(first['source']) + '</pre>',
                 '<h3>Existing reference</h3><pre>' + html.escape(first['reference']) + '</pre>']
        for row in rows:
            status = ', '.join(row.get('format_issues', [])) or row.get('error', 'format checks passed')
            parts.append(f"<section><h3>{row['variant']} · {html.escape(status)}</h3><pre>{html.escape(row.get('text', ''))}</pre></section>")
        cards.append(''.join(parts) + '</article>')
    document = ('<!doctype html><meta charset="utf-8"><title>Translation comparison</title>'
                '<style>body{font:16px system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem;background:#f5f6f8;color:#20242a}article{background:white;padding:1.5rem;margin:1rem 0;border-radius:12px}pre{white-space:pre-wrap;font:15px/1.6 system-ui}h3{font-size:14px;color:#566174}section{border-top:1px solid #dde2e7}</style>'
                '<h1>Translation comparison</h1><p>Existing references are not certified answers. Review meaning and style alongside the automated format checks.</p>' + ''.join(cards))
    (directory / 'comparison.html').write_text(document)
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sample-limit', type=int, default=12)
    parser.add_argument('--max-cost-usd', type=float, default=0.25)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'test/results')
    args = parser.parse_args()
    if not 1 <= args.sample_limit <= 12 or not 0 < args.max_cost_usd <= 1:
        parser.error('sample-limit must be 1–12 and max-cost-usd must be greater than 0 and at most 1')
    token = os.environ.get('LLM_TOKEN') or os.environ.get('OPENAI_API_KEY')
    if not token:
        parser.error('A CI API credential is required')
    cases, config, glossary = prepare_cases(args.sample_limit)
    background = (ROOT / 'test/translation_background.txt').read_text().strip()
    background_tokens = len(ENCODING.encode(background))
    if background_tokens < 1024:
        raise ValueError('Shared background must meet the 1,024-token cache threshold')
    metadata = {
        'created_at': datetime.now(timezone.utc).isoformat(), 'languages': LANGUAGES,
        'sample_limit': args.sample_limit, 'max_cost_usd': args.max_cost_usd,
        'background_tokens_estimate': background_tokens, 'prices_per_million': VARIANTS,
        'commit': os.environ.get('GITHUB_SHA'),
    }
    records = []
    total_cost = 0
    fatal = False
    with requests.Session() as session:
        session.headers.update({'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        for variant in VARIANTS:
            for case in cases:
                if total_cost >= args.max_cost_usd:
                    fatal = True
                    break
                row = {k: v for k, v in case.items() if k not in ('system_prompt', 'user_prompt')}
                row['variant'] = variant
                records.append(row)
                request = build_request(variant, case, background, language_context(case['language'], config, glossary))
                started = time.monotonic()
                try:
                    response = session.post(config['llm']['api_url'], json=request, timeout=(10, 90))
                    row['latency_seconds'] = time.monotonic() - started
                    if response.status_code != 200:
                        try:
                            error = response.json().get('error', {})
                            message = str(error.get('message', f'HTTP {response.status_code}'))
                        except ValueError:
                            message = f'HTTP {response.status_code}'
                        raise ValueError(message.replace(token, '[redacted]'))
                    payload = response.json()
                    row['usage'] = parse_usage(payload['usage'], VARIANTS[variant])
                    total_cost += row['usage']['cost_usd']
                    row['returned_model'] = payload['model']
                    choice = payload['choices'][0]
                    row['finish_reason'] = choice['finish_reason']
                    row['raw_text'] = choice['message']['content'] or ''
                    row['text'] = translate_mods.strip_extra_quotes(row['raw_text'].strip(), case['source'])
                    row['text'] = restore_source_line_breaks(case['source'], row['text'])
                    row['format_issues'] = format_issues(case['source'], row['text'])
                    if row['finish_reason'] != 'stop':
                        row['format_issues'].append('incomplete_generation')
                    if variant != 'original' and row['usage']['reasoning_tokens'] not in (None, 0):
                        row['format_issues'].append('unexpected_reasoning_tokens')
                    print(f"{variant}/{case['id']}/{case['language']}: cost=${row['usage']['cost_usd']:.6f}, cached={row['usage']['cached_tokens']}, writes={row['usage']['cache_write_tokens']}, issues={','.join(row['format_issues']) or 'none'}", flush=True)
                except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as error:
                    row['error'] = str(error).replace(token, '[redacted]')
                    fatal = True
                    print(f"{variant}: {row['error']}", flush=True)
                    break
                finally:
                    write_reports(args.output_dir, records, metadata)
            if total_cost >= args.max_cost_usd:
                break
    print(f'Total reported cost: ${total_cost:.6f}', flush=True)
    return 1 if fatal else 0


if __name__ == '__main__':
    raise SystemExit(main())
