"""Offline contracts for source-owned layout and structurally safe history."""

from collections import Counter
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import toml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
import translate_mods
from translation_prompt import (
    PLACEHOLDER, TAG, protected_format_issues, protected_format_signature,
)
from translator import TranslatorLLM


def completion(text):
    return Mock(status_code=200, json=Mock(return_value={
        'model': 'gpt-6-luna',
        'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 2000, 'completion_tokens': 100,
                  'prompt_tokens_details': {'cached_tokens': 1200, 'cache_write_tokens': 300},
                  'completion_tokens_details': {'reasoning_tokens': 0}},
    }))


class SourceFormatContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = json.loads((ROOT / 'test/translation_format_context_samples.json').read_text())['cases']

    def prompt(self, source, raw=None, current=None, **kwargs):
        system, user = translate_mods.build_translation_prompt(
            'Test.Description', source, 'Test Mod', 'zhCN',
            raw=raw, current_translation=current, **kwargs)
        return system, json.loads(user)

    def test_all_22_observed_conflicting_history_pairs_are_omitted(self):
        cases = [case for case in self.cases if case['file'] == '3723267610.toml']
        self.assertEqual(len(cases), 22)
        for case in cases:
            with self.subTest(key=case['key'], language=case['language']):
                before = copy.deepcopy(case)
                system, user = translate_mods.build_translation_prompt(
                    case['key'], case['source_text'], case['mod'], case['language'],
                    raw=case['old_source_text'], current_translation=case['current_translation'])
                payload = json.loads(user)
                self.assertEqual(case, before)
                self.assertEqual(payload['source_text'], case['source_text'])
                self.assertEqual(payload['source_format_contract'], protected_format_signature(case['source_text']))
                self.assertTrue(protected_format_issues(case['source_text'], case['old_source_text']))
                self.assertNotIn('old_source_text', payload['context'])
                self.assertNotIn('current_translation', payload['context'])
                self.assertEqual(payload['source_format_contract']['rich_text_tags'], {})
                self.assertFalse(any(payload['source_format_contract']['line_breaks'].values()))

    def test_compatible_history_keeps_changed_facts_as_reference(self):
        source, old = '<b>Stores {0}</b>\nCosts 20.', '<b>Stores {0}</b>\nCosts 10.'
        current = '<b>储存 {0}</b>\n价格 10。'
        _, payload = self.prompt(source, old, current)
        self.assertEqual(payload['context']['old_source_text'], old)
        self.assertEqual(payload['context']['current_translation'], current)
        self.assertEqual(payload['source_text'], source)

    def test_incompatible_current_target_alone_omits_the_pair(self):
        _, payload = self.prompt('Stores {0}.', 'Holds {0}.', '<b>储存 {0}</b>。')
        self.assertNotIn('old_source_text', payload['context'])
        self.assertNotIn('current_translation', payload['context'])

    def test_compatible_old_source_without_target_is_retained(self):
        _, payload = self.prompt('Costs 20.', 'Costs 10.')
        self.assertEqual(payload['context']['old_source_text'], 'Costs 10.')
        self.assertIsNone(payload['context']['current_translation'])

    def test_first_translation_gets_exact_four_newline_contract(self):
        case, = [case for case in self.cases if case['file'] == '3813984327.toml']
        self.assertIsNone(case['old_source_text'])
        self.assertIsNone(case['current_translation'])
        _, payload = self.prompt(case['source_text'])
        self.assertEqual(payload['source_format_contract']['line_breaks'], {
            'newline': 4, 'carriage_return': 0, 'literal_backslash_n': 0, 'literal_backslash_r': 0})
        self.assertNotIn('old_source_text', payload['context'])
        self.assertEqual(payload['source_text'].split('\n')[1], '')

    def test_signature_preserves_multiplicity_and_distinguishes_escape_kinds(self):
        source = '<b>{0}</b><b>{0}</b>\r\n{1:0.00}\\n\\r'
        _, payload = self.prompt(source)
        self.assertEqual(payload['source_format_contract'], {
            'placeholders': {'{0}': 2, '{1:0.00}': 1},
            'rich_text_tags': {'<b>': 2, '</b>': 2},
            'line_breaks': {'newline': 1, 'carriage_return': 1,
                            'literal_backslash_n': 1, 'literal_backslash_r': 1}})
        self.assertEqual(protected_format_issues(source, source.replace('\\n', '\n')), ['line_breaks_changed'])

    def test_signature_refactor_preserves_original_validator_semantics(self):
        # Intentional independent oracle of the previous public validation rules.
        corpus = ['', 'value', '{0}', '{0}{0}', '{1:0.00}', '{bad\nkey}',
                  '<b>x</b>', '<b><i>x</i></b>', '<i><b>x</b></i>',
                  '<color=yellow>x</color>', '<color=red>x</color>', '<broken',
                  '\n', '\r', '\r\n', '\\n', '\\r', '\n\\n', 'a\n\nb',
                  '\na\nb', 'a\nb\n', ' \n\t\n', 'a\r\nb', 'a\\nb']
        for source in corpus:
            for output in corpus:
                for preserve in (False, True):
                    expected = []
                    if Counter(PLACEHOLDER.findall(source)) != Counter(PLACEHOLDER.findall(output)):
                        expected.append('placeholder_changed')
                    if Counter(TAG.findall(source)) != Counter(TAG.findall(output)):
                        expected.append('markup_changed')
                    if any(source.count(token) != output.count(token) for token in ('\n', '\r', '\\n', '\\r')):
                        expected.append('line_breaks_changed')
                    if preserve and ([bool(line.strip()) for line in source.split('\n')]
                                     != [bool(line.strip()) for line in output.split('\n')]):
                        expected.append('line_entries_changed')
                    self.assertEqual(protected_format_issues(source, output, preserve), expected,
                                     (source, output, preserve))

    def test_name_pool_history_with_shifted_blank_slot_is_omitted(self):
        source = 'Ash\n\nElm'
        _, user = translate_mods.build_translation_prompt(
            'Beaver.NamePool', source, 'Names', 'zhCN',
            raw='Old\nName\n', current_translation='旧名\n名字\n')
        payload = json.loads(user)
        self.assertEqual(payload['source_format_contract']['line_entries'], {'count': 3, 'empty_indices': [1]})
        self.assertNotIn('old_source_text', payload['context'])
        self.assertNotIn('current_translation', payload['context'])
        # The extra mask remains conditional, matching the existing validator.
        self.assertNotIn('line_entries', self.prompt(source)[1]['source_format_contract'])

    def test_large_name_pool_contract_keeps_slot_metadata_compact(self):
        source = '\n'.join('Ash' for _ in range(1000))
        contract = protected_format_signature(source, True)
        self.assertEqual(contract['line_entries'], {'count': 1000, 'empty_indices': []})
        self.assertLess(len(json.dumps(contract)), 300)

    def test_split_name_pool_contract_uses_only_each_current_slice(self):
        source = 'Ash\n\nElm\nOak\n'
        client = Mock(max_length=7)
        client.translate.side_effect = lambda **options: options['text']
        self.assertEqual(translate_mods.translate_entry(
            client, 'Beaver.NamePool', {'new': source, 'raw': 'Old'}, 'zhCN', 'Names'), source)
        self.assertGreater(client.translate.call_count, 1)
        for call in client.translate.call_args_list:
            options = call.kwargs
            payload = json.loads(options['user_prompt'])
            self.assertTrue(options['preserve_line_entries'])
            self.assertEqual(payload['source_format_contract'], protected_format_signature(options['text'], True))
            self.assertNotIn('old_source_text', payload['context'])

    def test_entry_source_is_unchanged_and_contract_uses_validated_preprocessing(self):
        entry = {'raw': 'Old Beaver {0}.', 'new': 'Beaver {0}.', 'zhCN': '旧海狸 {0}。'}
        before = copy.deepcopy(entry)
        client = Mock(max_length=5000)
        client.translate.return_value = None
        glossary = {'Beaver': {'_global': {'zhCN': '海狸'}}}
        with patch('translator.requests.post', side_effect=AssertionError('No live requests')) as post:
            translate_mods.translate_entry(client, 'Test.Description', entry, 'zhCN', 'Test', glossary=glossary)
            post.assert_not_called()
        self.assertEqual(entry, before)
        options = client.translate.call_args.kwargs
        payload = json.loads(options['user_prompt'])
        self.assertEqual(options['text'], '海狸 {0}.')
        self.assertEqual(payload['source_text'], options['text'])
        self.assertEqual(payload['source_format_contract'], protected_format_signature(options['text']))

    def test_hints_and_cached_system_prefix_remain_independent_of_source_contract(self):
        hints = ['Reuse accepted terminology']
        first, payload = self.prompt('New {0}', '<b>Old</b>', '<b>旧</b>',
                                     prompt='Tooltip', specific_prompt='Keep units', glossary_hints=hints)
        second, _ = self.prompt('Other\ntext')
        self.assertEqual(first, second)
        self.assertEqual(payload['context']['field_hint'], 'Tooltip')
        self.assertEqual(payload['context']['specific_note'], 'Keep units')
        self.assertEqual(payload['context']['glossary_references'], hints)
        self.assertEqual(hints, ['Reuse accepted terminology'])

    @patch('translator.time.sleep')
    @patch('translator.requests.post')
    def test_retry_uses_exact_source_only_contract_and_keeps_one_retry(self, post, sleep):
        source = 'New value {0}'
        system, payload = self.prompt(source, '<b>Old value {0}</b>', '<b>旧值 {0}</b>')
        post.side_effect = [completion('<b>新值 {0}</b>'), completion('新值 {0}')]
        client = TranslatorLLM(api_token='synthetic-test-token', rate_limit='')
        self.assertEqual(client.translate(source, 'zhCN', system, json.dumps(payload)), '新值 {0}')
        self.assertEqual(post.call_count, 2)
        feedback = post.call_args_list[1].kwargs['json']['messages'][-1]['content']
        self.assertIn('Use only source_text for formatting', feedback)
        self.assertIn(json.dumps(protected_format_signature(source), ensure_ascii=False, separators=(',', ':')), feedback)
        report = client.get_cost_summary_dict()
        self.assertEqual(report['fail_count'], 1)
        self.assertEqual(report['recovered_fail_count'], 1)
        self.assertEqual(report['unresolved_translation_count'], 0)

    @patch('translator.time.sleep')
    @patch('translator.requests.post')
    def test_name_pool_retry_includes_existing_nonempty_slot_contract(self, post, sleep):
        source = 'Ash\n\nElm'
        post.side_effect = [completion('灰\n榆\n'), completion('灰\n\n榆')]
        client = TranslatorLLM(api_token='synthetic-test-token', rate_limit='')
        self.assertEqual(translate_mods.translate_entry(
            client, 'Beaver.NamePool', {'raw': source}, 'zhCN', 'Names'), '灰\n\n榆')
        self.assertEqual(post.call_count, 2)
        feedback = post.call_args_list[1].kwargs['json']['messages'][-1]['content']
        self.assertIn(json.dumps(protected_format_signature(source, True),
                                 ensure_ascii=False, separators=(',', ':')), feedback)

    @patch('translator.time.sleep')
    @patch('translator.requests.post')
    def test_invalid_prompt_remains_single_request_and_unresolved(self, post, sleep):
        post.return_value = Mock(status_code=400, text='{"error":{"type":"invalid_request_error","code":"invalid_prompt"}}')
        client = TranslatorLLM(api_token='synthetic-test-token', rate_limit='')
        system, payload = self.prompt('H')
        self.assertIsNone(client.translate('H', 'zhTW', system, json.dumps(payload)))
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    @patch('translator.time.sleep')
    @patch('translator.requests.post')
    def test_rejected_translation_keeps_old_target_raw_and_pending_new(self, post, sleep):
        post.return_value = completion('<b>旧值</b>')
        original = {'_meta': {'name': 'Fixture'}, 'Test.Description': {
            'raw': '<b>Old value</b>', 'new': 'New value', 'zhCN': '<b>旧值</b>'}}
        client = TranslatorLLM(api_token='synthetic-test-token', rate_limit='')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.toml'
            path.write_text(toml.dumps(original))
            self.assertEqual(translate_mods.process_toml_file(str(path), client, ['zhCN']), (0, 0))
            self.assertEqual(toml.load(path), original)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)


if __name__ == '__main__':
    unittest.main()
