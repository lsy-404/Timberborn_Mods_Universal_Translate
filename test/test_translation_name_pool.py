"""Offline contracts for complete, bounded, ordered name-pool translation."""

import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import toml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
import translate_mods
from translator import TranslatorLLM
import translation_prompt


def completion(text):
    return Mock(status_code=200, json=Mock(return_value={
        'model': 'claude-haiku-5-5',
        'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 2000, 'completion_tokens': 100,
                  'prompt_tokens_details': {
                      'cached_tokens': 1200, 'cache_write_tokens': 300,
                      'cache_creation_token_details': {
                          'ephemeral_5m_input_tokens': 300, 'ephemeral_1h_input_tokens': 0,
                      },
                  },
                  'completion_tokens_details': {'reasoning_tokens': 0}},
    }))


class NamePoolTests(unittest.TestCase):
    SOURCE = '\nAlpha\n\nBeta, Junior\nAlpha\nGamma\n'

    def setUp(self):
        post = patch('translator.requests.post')
        self.post = post.start()
        self.addCleanup(post.stop)
        sleep = patch('translator.time.sleep')
        sleep.start()
        self.addCleanup(sleep.stop)

    def client(self, **options):
        return TranslatorLLM(api_token='synthetic-test-token', rate_limit='', **options)

    @staticmethod
    def localized(source):
        return '\n'.join('Имя ' + name if name.strip() else name for name in source.split('\n'))

    def respond(self, *args, **kwargs):
        source = json.loads(kwargs['json']['messages'][1]['content'])['source_text']
        return completion(self.localized(source))

    def translate(self, client, entry=None):
        return translate_mods.translate_entry(
            client, 'Beaver.NamePool', entry or {'raw': self.SOURCE}, 'ruRU', 'Synthetic Names',
            prompt='Mod author context')

    def sent_sources(self):
        return [json.loads(call.kwargs['json']['messages'][1]['content'])['source_text']
                for call in self.post.call_args_list]

    def test_splitter_preserves_every_record_and_bounds_each_chunk(self):
        split = translation_prompt.split_name_pool_source
        for source in (self.SOURCE, '\n\n12345\n\n67890\n\n', '12345\n', '\n12345',
                       'Alpha\r\n\r\nBeta\r\n', ''):
            for limit in (5, 12, 20):
                if any(len(record) > limit for record in source.split('\n')):
                    continue
                with self.subTest(source=source, limit=limit):
                    chunks = split(source, limit)
                    self.assertEqual('\n'.join(chunks), source)
                    self.assertTrue(all(len(chunk) <= limit for chunk in chunks))
                    self.assertEqual([record for chunk in chunks for record in chunk.split('\n')],
                                     source.split('\n'))

    def test_splitter_preflights_all_records_before_returning_chunks(self):
        with self.assertRaises(ValueError):
            translation_prompt.split_name_pool_source('Short\n' + 'x' * 21, 20)

    def test_split_requests_keep_slots_duplicates_commas_and_author_context(self):
        self.post.side_effect = self.respond
        client = self.client(max_length=20)
        entry = {'raw': 'Previous pool', 'new': self.SOURCE, 'ruRU': 'Старый список',
                 'prompt': 'Keep the author naming preference'}
        self.assertEqual(self.translate(client, entry), self.localized(self.SOURCE))
        sources = self.sent_sources()
        self.assertGreater(len(sources), 1)
        self.assertEqual('\n'.join(sources), self.SOURCE)
        self.assertTrue(all(len(source) <= 20 for source in sources))
        for call in self.post.call_args_list:
            data = call.kwargs['json']
            self.assertEqual(data['model'], 'claude-haiku-5-5')
            self.assertEqual(data['max_tokens'], 1024)
            context = json.loads(data['messages'][1]['content'])['context']
            self.assertEqual(context['mod'], 'Synthetic Names')
            self.assertEqual(context['key'], 'Beaver.NamePool')
            self.assertEqual(context['field_hint'], 'Mod author context')
            self.assertIn('Keep the author naming preference', context['specific_note'])
            self.assertIn('order', context['specific_note'])
            self.assertNotIn('old_source_text', context)
            self.assertNotIn('current_translation', context)
            locale = translate_mods.LANGUAGE_NAMES.get('ruRU', 'ruRU')
            self.assertIn(f'Target locale: {locale}.', str(data['messages'][0]['content']))
        report = client.get_cost_summary_dict()
        self.assertEqual(report['request_count'], len(sources))
        self.assertEqual(report['unresolved_translation_count'], 0)
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.0001495 * len(sources))

    def test_real_pending_pool_is_split_without_changing_the_source(self):
        source = toml.load(ROOT / 'data/3723851280.toml')['Beaver.NamePool']['raw']
        self.assertGreater(len(source), 5000)
        self.post.side_effect = lambda *args, **kwargs: completion(
            json.loads(kwargs['json']['messages'][1]['content'])['source_text'])
        self.assertEqual(self.translate(self.client(), {'raw': source}), source)
        sources = self.sent_sources()
        self.assertGreater(len(sources), 1)
        self.assertEqual('\n'.join(sources), source)
        self.assertTrue(all(len(part) <= 5000 for part in sources))

    def test_empty_boundary_chunk_preserves_slot_without_an_extra_request(self):
        self.post.return_value = completion('Имя')
        source = 'x' * 20 + '\n'
        self.assertEqual(self.translate(self.client(max_length=20), {'raw': source}), 'Имя\n')
        self.post.assert_called_once()

    def test_empty_pool_records_remain_source_owned_without_api_calls(self):
        self.assertEqual(self.translate(self.client(), {'raw': '\n\n'}), '\n\n')
        self.post.assert_not_called()

    def test_changed_blank_slot_retries_even_when_line_break_count_matches(self):
        self.post.side_effect = [completion('Альфа\n\n\nГамма'), completion('Альфа\n\nБета\nГамма')]
        client = self.client()
        self.assertEqual(self.translate(client, {'raw': 'Alpha\n\nBeta\nGamma'}),
                         'Альфа\n\nБета\nГамма')
        self.assertEqual(self.post.call_count, 2)
        feedback = self.post.call_args.kwargs['json']['messages'][-1]['content']
        self.assertIn('line_entries_changed', feedback)
        self.assertEqual(client.get_cost_summary_dict()['recovered_fail_count'], 1)

    def test_persistent_slot_loss_is_unresolved(self):
        self.post.return_value = completion('Альфа\n\n\nГамма')
        client = self.client()
        self.assertIsNone(self.translate(client, {'raw': 'Alpha\n\nBeta\nGamma'}))
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def process_file(self, directory, client, source=None):
        entry = {'raw': 'Previous pool', 'new': source or self.SOURCE, 'ruRU': 'Старый список'}
        path = Path(directory) / 'names.toml'
        path.write_text(toml.dumps({'_meta': {'name': 'Synthetic Names'}, 'Beaver.NamePool': entry}),
                        encoding='utf-8')
        original = path.read_bytes()
        result = translate_mods.process_toml_file(str(path), client, ['ruRU'])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(toml.load(path)['Beaver.NamePool'], entry)
        self.assertEqual(result, (0, 0))

    def test_failed_middle_chunk_never_saves_a_partial_field(self):
        responses = iter([None, Mock(status_code=403, text='synthetic rejection')])

        def respond(*args, **kwargs):
            response = next(responses)
            return self.respond(*args, **kwargs) if response is None else response

        self.post.side_effect = respond
        client = self.client(max_length=20)
        with tempfile.TemporaryDirectory() as directory:
            self.process_file(directory, client)
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def test_only_complete_pool_is_saved_as_one_translation(self):
        self.post.side_effect = self.respond
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'names.toml'
            entry = {'raw': 'Previous pool', 'new': self.SOURCE, 'ruRU': 'Старый список',
                     'zhCN': '现有列表'}
            path.write_text(toml.dumps({'_meta': {'name': 'Synthetic Names'}, 'Beaver.NamePool': entry}),
                            encoding='utf-8')
            result = translate_mods.process_toml_file(str(path), self.client(max_length=20), ['ruRU'])
            saved = toml.load(path)['Beaver.NamePool']
        self.assertEqual(result, (1, 1))
        self.assertEqual(saved['raw'], self.SOURCE)
        self.assertEqual(saved['ruRU'], self.localized(self.SOURCE))
        self.assertEqual(saved['zhCN'], '现有列表')
        self.assertNotIn('new', saved)
        self.assertGreater(self.post.call_count, 1)

    def test_budget_exhaustion_between_chunks_preserves_pending_field(self):
        self.post.side_effect = self.respond
        client = self.client(max_length=20, max_cost=1)
        original_translate = client.translate

        def translate(*args, **kwargs):
            result = original_translate(*args, **kwargs)
            if result is not None:
                with client._cost_lock:
                    client.total_cost = client.max_cost  # Other concurrent entries spent the balance.
            return result

        with patch.object(client, 'translate', side_effect=translate):
            with tempfile.TemporaryDirectory() as directory:
                self.process_file(directory, client)
        self.post.assert_called_once()
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def test_unknown_usage_between_chunks_stops_without_saving(self):
        payload = completion('Имя').json()
        del payload['usage']['completion_tokens']
        self.post.return_value = Mock(status_code=200, json=Mock(return_value=payload))
        client = self.client(max_length=20)
        with tempfile.TemporaryDirectory() as directory:
            self.process_file(directory, client)
        self.post.assert_called_once()
        report = client.get_cost_summary_dict()
        self.assertEqual(report['unknown_cost_requests'], 1)
        self.assertEqual(report['unresolved_translation_count'], 1)

    def test_single_overlong_name_rejects_before_any_paid_chunk(self):
        client = self.client(max_length=20)
        with tempfile.TemporaryDirectory() as directory:
            self.process_file(directory, client, source='Short\n' + 'x' * 21)
        self.post.assert_not_called()
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def test_non_pool_long_source_keeps_the_existing_limit(self):
        client = self.client(max_length=20)
        result = translate_mods.translate_entry(client, 'Description', {'raw': self.SOURCE},
                                               'ruRU', 'Synthetic Names')
        self.assertIsNone(result)
        self.post.assert_not_called()
        self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def test_cli_fails_for_single_overlong_name_without_api_calls(self):
        root_logger = logging.getLogger()
        previous_handlers = set(root_logger.handlers)
        with tempfile.TemporaryDirectory() as directory:
            def process(**options):
                self.translate(options['translator'], {'raw': 'Short\n' + 'x' * 5001})

            try:
                with patch.object(sys, 'argv', ['translate_mods.py', '--api-token', 'synthetic-test-token',
                                              '--log-file', str(Path(directory) / 'translation.log')]), \
                        patch.object(translate_mods, 'setup_logging'), \
                        patch.object(translate_mods, 'load_glossary', return_value={}), \
                        patch.object(translate_mods, 'process_all_files', side_effect=process):
                    with self.assertRaises(SystemExit) as result:
                        translate_mods.main()
                report = json.loads((Path(directory) / 'cost_report.json').read_text())
            finally:
                for handler in list(root_logger.handlers):
                    if handler not in previous_handlers:
                        root_logger.removeHandler(handler)
                        handler.close()
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(report['unresolved_translation_count'], 1)
        self.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
