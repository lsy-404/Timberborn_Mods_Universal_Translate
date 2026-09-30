"""Run the production CLI on finite isolated localization samples."""

import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

import tiktoken
import toml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
import translate_mods
from translation_prompt import protected_format_issues, system_prompt_parts


def fixture(languages):
    old = '<b>Power output: {0} hp</b>\nStores 25 hph.'
    new = '<b>Power output: {0} hp</b>\nStores 150 hph.'
    return {
        '_meta': {'name': 'Localization Validation', 'prompt': 'A liquid pump and a mechanical storage building.'},
        'Building.Pump.DisplayName': {'raw': 'Folktails Pump', 'enUS': 'Folktails Pump'},
        'Building.Storage.Description': {'raw': old, 'new': new, **dict.fromkeys(languages, old)},
        'Building.Production.Description': {
            'raw': 'Produces {0} planks every 10 seconds.', 'enUS': 'Produces {0} planks every 10 seconds.'},
        'Blueprint.Id': {'raw': 'Blueprint_Id_{0}', 'copy': True},
        'Empty.Label': {'raw': 'Obsolete label', 'new': '', **dict.fromkeys(languages, 'Obsolete label')},
    }


def main():
    if not (os.environ.get('LLM_TOKEN') or os.environ.get('OPENAI_API_KEY')):
        raise RuntimeError('The existing CI API credential is required')
    config = toml.load(ROOT / '.github/config/config.toml')
    assert config['llm']['model'] == 'gpt-6-luna'
    assert config['llm']['reasoning_effort'] == 'none'
    languages = config['languages']['supported']
    output = ROOT / 'test/results/production-smoke'
    data_dir = output / 'data'
    data_dir.mkdir(parents=True, exist_ok=True)
    log_dir = ROOT / '.github/log'
    log_dir.mkdir(exist_ok=True)
    sample = fixture(languages)
    path = data_dir / 'localization.toml'
    path.write_text(toml.dumps(sample), encoding='utf-8')
    config['rate_limiter']['max_cost_per_run'] = 0.50
    config_path = output / 'config.toml'
    config_path.write_text(toml.dumps(config), encoding='utf-8')
    subprocess.run([
        sys.executable, str(ROOT / '.github/scripts/translate_mods.py'),
        '--config', str(config_path), '--data-dir', str(data_dir),
        '--glossary', str(ROOT / 'data/_glossary.toml'),
        '--log-file', str(log_dir / 'translation.log'), '--max-time', '120',
    ], cwd=ROOT, check=True)
    document = toml.load(path)
    for key, entry in sample.items():
        if key == '_meta':
            continue
        source = entry.get('new', entry['raw'])
        actual = document[key]
        assert actual['raw'] == source, key
        assert 'new' not in actual, key
        for language in languages:
            translated = actual[language]
            assert not protected_format_issues(source, translated), (key, language)
            if key == 'Building.Storage.Description':
                assert '150' in translated and '25' not in translated, (key, language, translated)
            if entry.get('copy'):
                assert translated == source, (key, language)
            if not source:
                assert translated == '', (key, language)
    assert '神尾' in document['Building.Pump.DisplayName']['zhCN']
    for language, stem in {'plPL': 'folkogon', 'ptBR': 'caudas-do-mato',
                           'koKR': '나무꼬리', 'trTR': 'köykuyruk'}.items():
        assert stem in document['Building.Pump.DisplayName'][language].casefold(), language
    cost_path = log_dir / 'cost_report.json'
    report = json.loads(cost_path.read_text())
    expected = 3 * len(languages) - 2
    assert report['request_count'] == expected
    assert report['success_count'] == expected and report['fail_count'] == 0
    assert report['reasoning_tokens'] == 0
    assert report['cost_tracking_complete'] and report['unknown_cost_requests'] == 0
    assert report['cached_tokens'] > 0 and report['cache_write_tokens'] > 0
    assert report['reserved_cost_usd'] == 0 and report['estimated_cost_usd'] < 0.50
    independent_cost = (
        report['ordinary_input_tokens'] * 0.10 + report['cached_tokens'] * 0.01
        + report['cache_write_tokens'] * 0.125 + report['output_tokens'] * 0.50) / 1e6
    assert math.isclose(report['estimated_cost_usd'], independent_cost, abs_tol=1e-12)
    translate_mods.LANGUAGE_NAMES = config['languages']['locale_names']
    encoding = tiktoken.get_encoding('o200k_base')
    token_counts = {
        language: sum(len(encoding.encode(part)) for part in system_prompt_parts(
            language, config['languages']['locale_names'][language]))
        for language in languages
    }
    assert all(1024 <= count <= 1480 for count in token_counts.values())
    verification = {
        'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        'context_tokens_estimate': token_counts, 'languages': languages,
        'entries_verified': len(sample) - 1, 'cost_report': report,
    }
    shutil.copyfile(cost_path, output / 'cost_report.json')
    (output / 'verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(verification, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
