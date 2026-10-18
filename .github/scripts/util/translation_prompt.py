"""Shared localization references and protected output formatting."""

from collections import Counter
from functools import lru_cache
import json
from pathlib import Path
import re

ASSETS = Path(__file__).resolve().parents[2] / 'config/translation'
PLACEHOLDER = re.compile(r'\{[^{}\n]+\}')
TAG = re.compile(r'</?[A-Za-z][^>\n]*>')


@lru_cache(maxsize=1)
def references():
    return (
        (ASSETS / 'background.txt').read_text(encoding='utf-8').strip(),
        json.loads((ASSETS / 'reference_terms.json').read_text(encoding='utf-8'))['terms'],
        json.loads((ASSETS / 'style_examples.json').read_text(encoding='utf-8')),
    )


@lru_cache(maxsize=64)
def system_prompt_parts(language, language_name, include_examples=True):
    common, terms, examples = references()
    localized = {term['source']: term['targets'][language] for term in terms if language in term['targets']}
    locale = (
        f"Target locale: {language_name}. Use this locale's script.\n"
        'Accepted reference wording (adapt grammatical forms): '
        + json.dumps(localized, ensure_ascii=False, separators=(',', ':'))
    )
    parts = [common, locale]
    pairs = [{'source': item['source'], 'translation': item['targets'][language]}
             for item in examples if language in item['targets']]
    if include_examples and pairs:
        parts.append('Independent style examples:\n' + json.dumps(pairs, ensure_ascii=False, separators=(',', ':')))
    return tuple(parts)


def restore_source_line_breaks(source, output):
    expected = source.count('\n')
    escaped = output.count('\\n')
    if (expected and '\r' not in source and '\\r' not in output
            and '\\n' not in source and escaped
            and output.count('\n') + escaped == expected):
        return output.replace('\\n', '\n')
    return output


def restore_source_boundary_whitespace(source, output):
    """Use source-owned boundary whitespace without changing interior layout."""
    whitespace = ' \t\r\n'
    if not source.strip(whitespace):
        return source
    leading = len(source) - len(source.lstrip(whitespace))
    trailing = len(source.rstrip(whitespace))
    return source[:leading] + output.strip(whitespace) + source[trailing:]


def strip_extra_quotes(text, reference_text):
    """Remove model-added quote wrappers, preserving source-owned quotation."""
    if not text or not reference_text:
        return text
    reference = reference_text.strip(' \t\r\n')
    if reference.startswith('"') and reference.endswith('"'):
        return text
    while len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip(' \t\r\n')
    return text


def split_name_pool_source(source, max_length):
    """Group complete LF-delimited records; joining chunks with LF is lossless."""
    records = source.split('\n')
    if max_length < 1 or any(len(record) > max_length for record in records):
        raise ValueError('A name-pool record exceeds the request length limit')
    chunks, current = [], []
    length = 0
    for record in records:
        added = len(record) + bool(current)
        if current and length + added > max_length:
            chunks.append('\n'.join(current))
            current, length = [], 0
        length += len(record) + bool(current)
        current.append(record)
    chunks.append('\n'.join(current))
    return chunks


def protected_format_issues(source, output, preserve_line_entries=False):
    issues = []
    if Counter(PLACEHOLDER.findall(source)) != Counter(PLACEHOLDER.findall(output)):
        issues.append('placeholder_changed')
    if Counter(TAG.findall(source)) != Counter(TAG.findall(output)):
        issues.append('markup_changed')
    if any(source.count(token) != output.count(token) for token in ('\n', '\r', '\\n', '\\r')):
        issues.append('line_breaks_changed')
    if preserve_line_entries and ([bool(line.strip()) for line in source.split('\n')]
                                  != [bool(line.strip()) for line in output.split('\n')]):
        issues.append('line_entries_changed')
    return issues
