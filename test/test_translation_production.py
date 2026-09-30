"""Production request, budget, formatting and persistence contracts."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import tiktoken
import toml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.github/scripts'))
import translate_mods
from translator import TranslatorLLM
from translation_prompt import protected_format_issues, restore_source_line_breaks, system_prompt_parts


def response(text='动力输出：{0} hp\n储存 150 hph。', finish='stop', reasoning=0):
    return Mock(status_code=200, json=Mock(return_value={
        'model': 'gpt-6-luna',
        'choices': [{'message': {'content': text}, 'finish_reason': finish}],
        'usage': {'prompt_tokens': 2000, 'completion_tokens': 100,
                  'prompt_tokens_details': {'cached_tokens': 1200, 'cache_write_tokens': 300},
                  'completion_tokens_details': {'reasoning_tokens': reasoning}},
    }))


class ProductionTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = toml.load(ROOT / '.github/config/config.toml')
        translate_mods.LANGUAGE_NAMES = cls.config['languages']['locale_names']
        (ROOT / 'test/results').mkdir(exist_ok=True)

    def translator(self, **options):
        return TranslatorLLM(api_token='test-token', rate_limit='', **options)

    def prompts(self):
        return translate_mods.build_translation_prompt(
            'Building.Storage.Description', 'Power output: {0} hp\nStores 150 hph.',
            'Pump Mod', 'zhCN', raw='Stores 25 hph.', current_translation='储存 25 hph。',
        )

    def test_production_defaults_and_context_bounds_for_every_language(self):
        self.assertEqual(self.config['llm']['model'], 'gpt-6-luna')
        self.assertEqual(self.config['llm']['reasoning_effort'], 'none')
        encoding = tiktoken.get_encoding('o200k_base')
        for language in self.config['languages']['supported']:
            parts = system_prompt_parts(language, translate_mods.LANGUAGE_NAMES[language])
            count = sum(len(encoding.encode(part)) for part in parts)
            self.assertGreaterEqual(count, 1024, language)
            self.assertLessEqual(count, 1480, language)
        self.assertEqual(len(system_prompt_parts('plPL', 'Polish')), 2)
        for language, wording in {'plPL': 'Folkogonów', 'ptBR': 'Caudas-do-mato',
                                  'koKR': '나무꼬리', 'trTR': 'Köykuyruklar'}.items():
            self.assertIn(wording, system_prompt_parts(language, translate_mods.LANGUAGE_NAMES[language])[1])

    def test_static_prefix_and_structured_source_boundary(self):
        first, user = self.prompts()
        second, other = translate_mods.build_translation_prompt('Other.Key', 'Other source', 'Other Mod', 'zhCN')
        self.assertEqual(first, second)
        self.assertNotEqual(user, other)
        payload = json.loads(user)
        self.assertEqual(payload['source_text'], 'Power output: {0} hp\nStores 150 hph.')
        self.assertEqual(payload['context']['old_source_text'], 'Stores 25 hph.')
        self.assertEqual(payload['context']['current_translation'], '储存 25 hph。')
        self.assertTrue(all(part['prompt_cache_breakpoint'] == {'mode': 'explicit'} for part in first))

    @patch('translator.requests.post')
    def test_actual_request_and_disjoint_cache_billing(self, post):
        post.return_value = response()
        translator = self.translator()
        system, user = self.prompts()
        self.assertIsNotNone(translator.translate(json.loads(user)['source_text'], 'zhCN', system, user))
        body = post.call_args.kwargs['json']
        self.assertEqual(body['model'], 'gpt-6-luna')
        self.assertEqual(body['reasoning_effort'], 'none')
        self.assertEqual(body['service_tier'], 'default')
        self.assertEqual(body['prompt_cache_options'], {'mode': 'explicit', 'ttl': '30m'})
        report = translator.get_cost_summary_dict()
        self.assertEqual(report['ordinary_input_tokens'], 500)
        self.assertEqual(report['cached_tokens'], 1200)
        self.assertEqual(report['cache_write_tokens'], 300)
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.0001495)
        self.assertEqual(report['reserved_cost_usd'], 0)

    @patch('translator.requests.post')
    def test_truncated_generation_is_billed_and_rejected(self, post):
        post.return_value = response(finish='length')
        translator = self.translator()
        system, user = self.prompts()
        self.assertIsNone(translator.translate(json.loads(user)['source_text'], 'zhCN', system, user))
        report = translator.get_cost_summary_dict()
        self.assertEqual(report['success_count'], 0)
        self.assertEqual(report['fail_count'], 1)
        self.assertAlmostEqual(report['estimated_cost_usd'], 0.0001495)

    @patch('translator.requests.post')
    def test_reasoning_output_is_billed_and_rejected(self, post):
        post.return_value = response(reasoning=5)
        translator = self.translator()
        system, user = self.prompts()
        self.assertIsNone(translator.translate(json.loads(user)['source_text'], 'zhCN', system, user))
        self.assertEqual(translator.get_cost_summary_dict()['reasoning_tokens'], 5)

    @patch('translator.requests.post')
    def test_unknown_usage_reserves_cost_and_stops_new_requests(self, post):
        payload = response().json()
        del payload['usage']['prompt_tokens_details']['cache_write_tokens']
        post.return_value = Mock(status_code=200, json=Mock(return_value=payload))
        translator = self.translator(max_cost=1)
        system, user = self.prompts()
        for _ in range(2):
            self.assertIsNone(translator.translate(json.loads(user)['source_text'], 'zhCN', system, user))
        post.assert_called_once()
        report = translator.get_cost_summary_dict()
        self.assertFalse(report['cost_tracking_complete'])
        self.assertEqual(report['unknown_cost_requests'], 1)
        self.assertGreater(report['estimated_cost_usd'], 0)
        self.assertEqual(report['reserved_cost_usd'], 0)

    @patch('translator.requests.post')
    def test_budget_failure_does_not_persist_source_as_translation(self, post):
        translator = self.translator(max_cost=0.000001)
        system, user = self.prompts()
        self.assertIsNone(translator.translate(json.loads(user)['source_text'], 'zhCN', system, user))
        post.assert_not_called()

    @patch('translator.requests.post')
    def test_in_flight_requests_reserve_the_budget(self, post):
        entered, release = threading.Event(), threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('test request was not released')
            return response()
        post.side_effect = blocked
        translator = self.translator(max_cost=0.008)
        translator._cache_warmed = True
        system, user = self.prompts()
        source = json.loads(user)['source_text']
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(translator.translate, source, 'zhCN', system, user)
            try:
                self.assertTrue(entered.wait(5))
                second = pool.submit(translator.translate, source, 'zhCN', system, user)
                self.assertIsNone(second.result(timeout=5))
                self.assertLessEqual(translator.get_cost_summary_dict()['reserved_cost_usd'], 0.008)
            finally:
                release.set()
            self.assertIsNotNone(first.result(timeout=5))
        post.assert_called_once()

    @patch('translator.requests.post')
    def test_overlong_source_is_not_silently_truncated(self, post):
        self.assertIsNone(self.translator(max_length=2).translate('Long text', 'zhCN', [], '{}'))
        post.assert_not_called()

    @patch('translator.requests.post')
    def test_lost_placeholders_are_billed_and_rejected(self, post):
        post.return_value = response('动力输出\n储存 150 hph。')
        translator = self.translator()
        system, user = self.prompts()
        self.assertIsNone(translator.translate(json.loads(user)['source_text'], 'zhCN', system, user))
        self.assertEqual(translator.get_cost_summary_dict()['fail_count'], 1)
        self.assertGreater(translator.get_cost_summary_dict()['estimated_cost_usd'], 0)

    @patch('translator.requests.post')
    def test_newline_representation_is_restored_before_validation(self, post):
        post.return_value = response('动力输出：{0} hp\\n储存 150 hph。')
        system, user = self.prompts()
        output = self.translator().translate(json.loads(user)['source_text'], 'zhCN', system, user)
        self.assertIn('\n', output)
        self.assertNotIn('\\n', output)
        self.assertEqual(restore_source_line_breaks('Use \\n literally', '保留 \\n'), '保留 \\n')
        self.assertEqual(restore_source_line_breaks('First\r\nSecond', '第一行\\n第二行'), '第一行\\n第二行')

    def test_local_glossary_stays_in_dynamic_context(self):
        glossary = translate_mods.merge_glossaries(
            {'Resource': {'zhCN': '资源'}}, {'Resource': {'zhCN': '特殊资源'}})
        source, hints = translate_mods.generate_glossary_hints('Resource', 'zhCN', glossary, ['zhCN'])
        system, user = translate_mods.build_translation_prompt(
            'Resource.Title', source, 'Storage Mod', 'zhCN', glossary_hints=hints)
        self.assertEqual(json.loads(user)['source_text'], '特殊资源')
        self.assertNotIn('特殊资源', ''.join(part['text'] for part in system))

    def test_partial_update_preserves_old_source_and_pending_new_text(self):
        translator = Mock()
        translator.translate.side_effect = lambda **kw: '储存 150 hph。' if kw['target_language'] == 'zhCN' else None
        with tempfile.TemporaryDirectory(dir=ROOT / 'test/results') as directory:
            path = Path(directory) / 'partial.toml'
            path.write_text(toml.dumps({'_meta': {'name': 'Storage'}, 'Storage.Description': {
                'raw': 'Stores 25 hph.', 'new': 'Stores 150 hph.', 'zhCN': '储存 25 hph。', 'deDE': '25 hph',
            }}))
            made, _ = translate_mods.process_toml_file(str(path), translator, ['zhCN', 'deDE'], max_lang_threads=1)
            entry = toml.load(path)['Storage.Description']
            self.assertEqual(made, 1)
            self.assertEqual(entry['raw'], 'Stores 25 hph.')
            self.assertEqual(entry['new'], 'Stores 150 hph.')
            self.assertEqual(entry['zhCN'], '储存 150 hph。')
            self.assertEqual(entry['deDE'], '25 hph')

    def test_copy_and_empty_updates_do_not_call_the_api(self):
        translator = self.translator()
        with tempfile.TemporaryDirectory(dir=ROOT / 'test/results') as directory:
            path = Path(directory) / 'copy.toml'
            path.write_text(toml.dumps({'_meta': {'name': 'Storage'},
                                      'Id': {'raw': 'Id_{0}', 'copy': True},
                                      'Empty': {'raw': 'Old', 'new': '', 'zhCN': '旧'}}))
            translate_mods.process_toml_file(str(path), translator, ['zhCN', 'deDE'])
            document = toml.load(path)
            self.assertEqual(document['Id']['deDE'], 'Id_{0}')
            self.assertEqual(document['Empty']['raw'], '')
            self.assertEqual(document['Empty']['zhCN'], '')
            self.assertNotIn('new', document['Empty'])
            self.assertEqual(translator.get_cost_summary_dict()['request_count'], 0)

    def test_invalid_cache_counts_are_rejected(self):
        payload = response().json()['usage']
        payload['prompt_tokens'] = 10
        with self.assertRaises(ValueError):
            TranslatorLLM.usage_metrics(payload)

    @patch('translator.requests.post')
    def test_concurrent_settlement_leaves_no_budget_reservation(self, post):
        post.return_value = response()
        translator = self.translator()
        system, user = self.prompts()
        source = json.loads(user)['source_text']
        with ThreadPoolExecutor(max_workers=10) as pool:
            outputs = list(pool.map(lambda _: translator.translate(source, 'zhCN', system, user), range(37)))
        self.assertTrue(all(output is not None for output in outputs))
        report = translator.get_cost_summary_dict()
        self.assertEqual(report['request_count'], 37)
        self.assertEqual(report['reserved_cost_usd'], 0)
        self.assertAlmostEqual(report['estimated_cost_usd'], 37 * 0.0001495)

    @patch('translator.requests.post')
    def test_boundary_line_breaks_are_preserved(self, post):
        post.return_value = response('\n第一行\n')
        system, user = translate_mods.build_translation_prompt('Warning', '\nFirst\n', 'Pump', 'zhCN')
        self.assertEqual(self.translator().translate('\nFirst\n', 'zhCN', system, user), '\n第一行\n')

    def test_reformat_only_cli_requires_no_api_credential(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'test/results') as directory:
            path = Path(directory) / 'format.toml'
            path.write_text(toml.dumps({'_meta': {'name': 'Pump'}, 'Label': {'raw': 'Label', 'zhCN': '标签'}}))
            env = dict(os.environ)
            env.pop('LLM_TOKEN', None)
            env.pop('OPENAI_API_KEY', None)
            result = subprocess.run([
                sys.executable, str(ROOT / '.github/scripts/translate_mods.py'),
                '--reformat-only', '--data-dir', directory,
            ], cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(toml.load(path)['Label']['zhCN'], '标签')


if __name__ == '__main__':
    unittest.main()
