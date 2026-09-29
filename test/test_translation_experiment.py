"""Offline checks for experiment accounting and localization constraints."""

import unittest

import compare_translation_models as experiment


class TranslationExperimentTests(unittest.TestCase):
    def test_cache_tokens_are_not_double_charged(self):
        result = experiment.parse_usage({
            'prompt_tokens': 2000, 'completion_tokens': 100,
            'prompt_tokens_details': {'cached_tokens': 1200, 'cache_write_tokens': 300},
            'completion_tokens_details': {'reasoning_tokens': 0},
        }, experiment.VARIANTS['luna_none'])
        self.assertEqual(result['ordinary_input_tokens'], 500)
        self.assertAlmostEqual(result['cost_usd'], 0.0001495)
        with self.assertRaises(ValueError):
            experiment.parse_usage({'prompt_tokens': 20, 'completion_tokens': 1,
                                    'prompt_tokens_details': {'cached_tokens': 30}}, experiment.VARIANTS['original'])

    def test_detects_lost_runtime_syntax(self):
        source = '<b>Requires {0}</b>\n75% faster.'
        issues = experiment.format_issues(source, '需要资源，速度提高50%。')
        self.assertIn('placeholder_changed', issues)
        self.assertIn('markup_changed', issues)
        self.assertIn('line_breaks_changed', issues)
        self.assertIn('numbers_changed', issues)

    def test_failed_generation_remains_in_cost_total(self):
        usage = experiment.parse_usage({'prompt_tokens': 100, 'completion_tokens': 20},
                                        experiment.VARIANTS['luna_none'])
        summary = experiment.aggregate([{'variant': 'luna_none', 'usage': usage,
                                          'error': 'Malformed generation'}])['luna_none']
        self.assertEqual(summary['completed'], 0)
        self.assertAlmostEqual(summary['cost_usd'], 0.00002)

    def test_localized_decimal_does_not_change_value(self):
        self.assertEqual(experiment.numbers('0.75 means 75% faster'), experiment.numbers('0,75 bedeutet 75% schneller'))
        self.assertNotEqual(experiment.numbers('0.75 means 75% faster'), experiment.numbers('0.5 means 50% faster'))

    def test_background_is_cacheable_and_stays_before_dynamic_context(self):
        cases, config, glossary = experiment.prepare_cases()
        background = (experiment.ROOT / 'test/translation_background.txt').read_text().strip()
        self.assertGreaterEqual(len(experiment.ENCODING.encode(background)), 1024)
        first = experiment.build_request('luna_background', cases[0], background,
                                         experiment.language_context(cases[0]['language'], config, glossary))
        other = experiment.build_request('luna_background', cases[-1], background,
                                         experiment.language_context(cases[-1]['language'], config, glossary))
        self.assertEqual(first['messages'][0]['content'][0], other['messages'][0]['content'][0])
        self.assertNotEqual(first['messages'][1]['content'], other['messages'][1]['content'])
        self.assertEqual(first['prompt_cache_options']['mode'], 'explicit')
        self.assertEqual(first['reasoning_effort'], 'none')
        self.assertNotIn('reasoning_effort', experiment.build_request('original', cases[0], '', ''))

    def test_samples_include_realistic_updated_source_without_reference_leakage(self):
        cases, _, _ = experiment.prepare_cases()
        self.assertEqual(len(cases), 48)
        updated = next(c for c in cases if c['id'] == 'updated_swimming_speed' and c['language'] == 'deDE')
        self.assertIn('0.75', updated['source'])
        self.assertIn('Original Text (Old)', updated['user_prompt'])
        self.assertIn('Current Translation', updated['user_prompt'])
        missing = next(c for c in cases if c['id'] == 'liquid_tank')
        self.assertNotIn('Current Translation', missing['user_prompt'])


if __name__ == '__main__':
    unittest.main()
