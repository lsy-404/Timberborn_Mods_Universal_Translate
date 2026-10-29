"""Offline contracts for bounded response recovery and honest run status."""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
import translate_mods
from translator import TranslatorLLM


def completion(text='值 {0}'):
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


class TranslationRecoveryTests(unittest.TestCase):
    def setUp(self):
        post = patch('translator.requests.post')
        self.post = post.start()
        self.addCleanup(post.stop)
        sleep = patch('translator.time.sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def translator(self, **options):
        return TranslatorLLM(api_token='synthetic-test-token', rate_limit='', **options)

    def translate(self, client, source='Value {0}'):
        return client.translate(source, 'zhCN', [], json.dumps({'source_text': source}))

    def test_single_line_response_padding_needs_no_retry(self):
        self.post.return_value = completion('\r\n\t 值 {0} \n')
        client = self.translator()
        self.assertEqual(self.translate(client), '值 {0}')
        self.post.assert_called_once()

    def test_source_boundary_whitespace_is_restored_exactly(self):
        source = '\r\n  First\nSecond \r\n'
        self.post.return_value = completion('第一\n第二\n')
        self.assertEqual(self.translate(self.translator(), source), '\r\n  第一\n第二 \r\n')
        self.post.assert_called_once()

    def test_literal_backslash_n_survives_response_padding_cleanup(self):
        self.post.return_value = completion('保留 \\n\n')
        self.assertEqual(self.translate(self.translator(), 'Use \\n literally'), '保留 \\n')
        self.post.assert_called_once()

    def test_format_retry_keeps_both_attempt_costs_and_recovers_run_status(self):
        self.post.side_effect = [completion('值'), completion()]
        client = self.translator()
        self.assertEqual(self.translate(client), '值 {0}')
        report = client.get_cost_summary_dict()
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(report['request_count'], 2)
        self.assertEqual(report['fail_count'], 1)
        self.assertEqual(report['success_count'], 1)
        self.assertEqual(report['recovered_fail_count'], 1)
        self.assertEqual(report['unresolved_translation_count'], 0)
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.000299)
        self.assertEqual(report['reserved_cost_usd'], 0)
        first = self.post.call_args_list[0].kwargs['json']['messages']
        second = self.post.call_args_list[1].kwargs['json']['messages']
        self.assertEqual(second[:2], first)
        self.assertIn('placeholder_changed', second[-1]['content'])
        self.assertEqual(second[-1]['role'], 'user')

    def test_empty_completion_gets_one_billed_recovery_attempt(self):
        self.post.side_effect = [completion(''), completion()]
        client = self.translator()
        self.assertEqual(self.translate(client), '值 {0}')
        report = client.get_cost_summary_dict()
        self.assertEqual(self.post.call_count, 2)
        self.assertEqual(report['recovered_fail_count'], 1)
        self.assertEqual(report['unresolved_translation_count'], 0)
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.000299)

    def test_503_recovery_is_rate_limited_and_keeps_accounting_complete(self):
        self.post.side_effect = [Mock(status_code=503, text='synthetic server error'), completion()]
        client = self.translator()
        with patch.object(client, '_check_rate_limit') as limit:
            self.assertEqual(self.translate(client), '值 {0}')
        self.assertEqual(limit.call_count, 2)
        report = client.get_cost_summary_dict()
        self.assertEqual(report['request_count'], 2)
        self.assertEqual(report['fail_count'], 1)
        self.assertEqual(report['recovered_fail_count'], 1)
        self.assertEqual(report['unresolved_translation_count'], 0)
        self.assertTrue(report['cost_tracking_complete'])
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.0001495)
        self.sleep.assert_called_once()

    def test_persistent_503_stops_after_one_retry(self):
        self.post.return_value = Mock(status_code=503, text='synthetic server error')
        client = self.translator()
        self.assertIsNone(self.translate(client))
        self.assertEqual(self.post.call_count, 2)
        report = client.get_cost_summary_dict()
        self.assertEqual(report['fail_count'], 2)
        self.assertEqual(report['recovered_fail_count'], 0)
        self.assertEqual(report['unresolved_translation_count'], 1)
        self.assertEqual(report['estimated_cost_usd'], 0)
        self.assertEqual(report['reserved_cost_usd'], 0)

    def test_authentication_and_permission_errors_are_not_retried(self):
        for status in (401, 403, 429):
            with self.subTest(status=status):
                self.post.reset_mock()
                self.post.return_value = Mock(status_code=status, text='synthetic rejection')
                client = self.translator()
                self.assertIsNone(self.translate(client))
                self.post.assert_called_once()
                self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def test_unknown_usage_never_retries_or_releases_unknown_cost(self):
        payload = completion().json()
        del payload['usage']['completion_tokens']
        self.post.return_value = Mock(status_code=200, json=Mock(return_value=payload))
        client = self.translator()
        self.assertIsNone(self.translate(client))
        self.assertIsNone(self.translate(client))
        self.post.assert_called_once()
        report = client.get_cost_summary_dict()
        self.assertEqual(report['unknown_cost_requests'], 1)
        self.assertEqual(report['unresolved_translation_count'], 2)
        self.assertFalse(report['cost_tracking_complete'])
        self.assertGreater(report['estimated_cost_usd'], 0)

    def test_unknown_usage_on_retry_stops_further_requests(self):
        payload = completion().json()
        del payload['usage']['completion_tokens']
        self.post.side_effect = [completion('值'), Mock(status_code=200, json=Mock(return_value=payload))]
        client = self.translator()
        self.assertIsNone(self.translate(client))
        self.assertIsNone(self.translate(client))
        self.assertEqual(self.post.call_count, 2)
        report = client.get_cost_summary_dict()
        self.assertEqual(report['unknown_cost_requests'], 1)
        self.assertEqual(report['unresolved_translation_count'], 2)
        self.assertEqual(report['recovered_fail_count'], 0)
        self.assertFalse(report['cost_tracking_complete'])
        self.assertGreater(report['estimated_cost_usd'], 0.0001495)

    def test_concurrent_recoveries_keep_budget_and_counters_consistent(self):
        client = self.translator(max_cost=0.01)
        client._initial_request_completed = True
        first_attempts = threading.Barrier(2)
        lock = threading.Lock()
        attempts = {}

        def respond(*args, **kwargs):
            thread = threading.get_ident()
            with lock:
                attempts[thread] = attempts.get(thread, 0) + 1
                first = attempts[thread] == 1
            if first:
                first_attempts.wait(timeout=5)
            report = client.get_cost_summary_dict()
            self.assertLessEqual(report['estimated_cost_usd'] + report['reserved_cost_usd'], 0.01)
            return completion('值' if first else '值 {0}')

        self.post.side_effect = respond
        with ThreadPoolExecutor(max_workers=2) as pool:
            output = list(pool.map(lambda _: self.translate(client), range(2)))
        self.assertEqual(output, ['值 {0}', '值 {0}'])
        report = client.get_cost_summary_dict()
        self.assertEqual(report['request_count'], 4)
        self.assertEqual(report['fail_count'], 2)
        self.assertEqual(report['recovered_fail_count'], 2)
        self.assertEqual(report['unresolved_translation_count'], 0)
        self.assertEqual(report['reserved_cost_usd'], 0)
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.000598)

    def test_retry_respects_existing_run_budget(self):
        self.post.return_value = completion('值')
        client = self.translator(max_cost=0.0007)
        self.assertIsNone(self.translate(client))
        self.post.assert_called_once()
        report = client.get_cost_summary_dict()
        self.assertLessEqual(report['estimated_cost_usd'], 0.00425)
        self.assertEqual(report['reserved_cost_usd'], 0)
        self.assertEqual(report['unresolved_translation_count'], 1)

    def test_exhausted_budget_skip_is_recorded_without_an_api_call(self):
        client = self.translator(max_cost=1)
        # Earlier entries in the same run have already consumed the budget.
        client.total_cost = 1
        self.assertIsNone(self.translate(client))
        self.post.assert_not_called()
        report = client.get_cost_summary_dict()
        self.assertEqual(report['unresolved_translation_count'], 1)
        self.assertEqual(report['estimated_cost_usd'], 1)
        self.assertEqual(report['reserved_cost_usd'], 0)

    def test_budget_skip_after_recovered_attempt_remains_unresolved(self):
        self.post.side_effect = [completion('值'), completion()]
        client = self.translator(max_cost=1)
        self.assertEqual(self.translate(client), '值 {0}')
        # Other parallel entries then consume the rest of the run budget.
        client.total_cost = 1
        self.assertIsNone(self.translate(client, 'Another pending entry'))
        self.assertEqual(self.post.call_count, 2)
        report = client.get_cost_summary_dict()
        self.assertEqual(report['recovered_fail_count'], 1)
        self.assertEqual(report['unresolved_translation_count'], 1)

    def test_internal_line_loss_and_tag_changes_stay_rejected(self):
        for source, output in [('First\nSecond', '合并成一行'),
                               ('<b>Value {0}</b>', '<i>值 {0}</i>')]:
            with self.subTest(source=source):
                self.post.reset_mock()
                self.post.return_value = completion(output)
                client = self.translator()
                self.assertIsNone(self.translate(client, source))
                self.assertEqual(self.post.call_count, 2)
                self.assertEqual(client.get_cost_summary_dict()['unresolved_translation_count'], 1)

    def test_partial_entry_retains_pending_source_after_exhausted_retry(self):
        self.post.side_effect = [completion('值'), completion(), completion('错误'), completion('错误')]
        client = self.translator()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'partial.toml'
            path.write_text('[Label]\nraw = "Old {0}"\nnew = "Value {0}"\nzhCN = "旧 {0}"\ndeDE = "Alt {0}"\n')
            made, _ = translate_mods.process_toml_file(str(path), client, ['zhCN', 'deDE'], max_lang_threads=1)
            import toml
            entry = toml.load(path)['Label']
            self.assertEqual(made, 1)
            self.assertEqual(entry['raw'], 'Old {0}')
            self.assertEqual(entry['new'], 'Value {0}')
            self.assertEqual(entry['zhCN'], '值 {0}')
            self.assertEqual(entry['deDE'], 'Alt {0}')

    def test_empty_quote_wrappers_never_count_as_recovered_entry(self):
        for wrapped in ('" "', '""', '"\" \""'):
            with self.subTest(wrapped=wrapped):
                self.post.reset_mock()
                self.post.side_effect = [completion(''), completion(wrapped)]
                client = self.translator()
                result = translate_mods.translate_entry(client, 'Label', {'raw': 'Label'}, 'zhCN', 'Test')
                self.assertIsNone(result)
                report = client.get_cost_summary_dict()
                self.assertEqual(report['fail_count'], 2)
                self.assertEqual(report['recovered_fail_count'], 0)
                self.assertEqual(report['unresolved_translation_count'], 1)

    def test_source_owned_quotation_and_boundary_whitespace_survive(self):
        self.post.return_value = completion('"标签"\n')
        source = '\n "Label" \r\n'
        client = self.translator()
        result = translate_mods.translate_entry(client, 'Label', {'raw': source}, 'zhCN', 'Test')
        self.assertEqual(result, '\n "标签" \r\n')
        self.post.assert_called_once()

    def test_glossary_owned_quotation_uses_the_same_validation_reference(self):
        glossary = translate_mods.merge_glossaries(
            {'Other': {'zhCN': '其他'}}, {'Label': {'zhCN': '"标签"'}})
        entry = {'raw': 'Old', 'new': 'Label'}
        self.post.return_value = completion('"标签"')
        client = self.translator()
        result = translate_mods.translate_entry(client, 'Label', entry, 'zhCN', 'Test',
                                               glossary=glossary, language_priority=['zhCN'])
        self.assertEqual(result, '"标签"')
        user = self.post.call_args.kwargs['json']['messages'][1]['content']
        self.assertEqual(json.loads(user)['source_text'], '"标签"')

    def test_glossary_quotation_cannot_hide_an_empty_retry(self):
        glossary = translate_mods.merge_glossaries(
            {'Other': {'zhCN': '其他'}}, {'Label': {'zhCN': '"标签"'}})
        self.post.side_effect = [completion(''), completion('" "')]
        client = self.translator()
        result = translate_mods.translate_entry(client, 'Label', {'raw': 'Old', 'new': 'Label'},
                                               'zhCN', 'Test', glossary=glossary, language_priority=['zhCN'])
        self.assertIsNone(result)
        report = client.get_cost_summary_dict()
        self.assertEqual(report['recovered_fail_count'], 0)
        self.assertEqual(report['unresolved_translation_count'], 1)

    def run_main(self, directory, source='Value {0}'):
        root_logger = logging.getLogger()
        previous_handlers = set(root_logger.handlers)

        def process(**options):
            self.translate(options['translator'], source)

        try:
            with patch.object(sys, 'argv', ['translate_mods.py', '--api-token', 'synthetic-test-token',
                                          '--log-file', str(Path(directory) / 'translation.log')]), \
                    patch.object(translate_mods, 'setup_logging'), \
                    patch.object(translate_mods, 'load_glossary', return_value={}), \
                    patch.object(translate_mods, 'process_all_files', side_effect=process):
                return translate_mods.main()
        finally:
            for handler in list(root_logger.handlers):
                if handler not in previous_handlers:
                    root_logger.removeHandler(handler)
                    handler.close()

    def test_cli_accepts_recovered_attempt_and_reports_it_honestly(self):
        self.post.side_effect = [completion('值'), completion()]
        with tempfile.TemporaryDirectory() as directory:
            self.run_main(directory)
            report = json.loads((Path(directory) / 'cost_report.json').read_text())
        self.assertEqual(report['fail_count'], 1)
        self.assertEqual(report['recovered_fail_count'], 1)
        self.assertEqual(report['unresolved_translation_count'], 0)

    def test_cli_still_fails_for_unrecovered_requests(self):
        self.post.return_value = completion('值')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as result:
                self.run_main(directory)
            report = json.loads((Path(directory) / 'cost_report.json').read_text())
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(report['unresolved_translation_count'], 1)
        self.assertEqual(report['fail_count'], 2)

    def test_cli_fails_if_retry_contains_only_a_quote_wrapper(self):
        self.post.side_effect = [completion(''), completion('" "')]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SystemExit) as result:
                self.run_main(directory, source='Label')
            report = json.loads((Path(directory) / 'cost_report.json').read_text())
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(report['recovered_fail_count'], 0)
        self.assertEqual(report['unresolved_translation_count'], 1)

    def test_cli_fails_for_work_skipped_after_budget_exhaustion(self):
        client = self.translator(max_cost=1)
        client.total_cost = 1
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(translate_mods, 'TranslatorLLM', return_value=client):
                with self.assertRaises(SystemExit) as result:
                    self.run_main(directory)
            report = json.loads((Path(directory) / 'cost_report.json').read_text())
        self.assertEqual(result.exception.code, 1)
        self.assertEqual(report['unresolved_translation_count'], 1)
        self.post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
