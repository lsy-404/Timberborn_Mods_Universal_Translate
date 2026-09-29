"""Check compressed contexts, held-out samples and blind-grading accounting."""

import json
import re
import unittest

import compare_translation_models as core
import run_translation_scale_experiment as scale


class TranslationScaleTests(unittest.TestCase):
    def test_contexts_fit_cache_bounds_for_every_locale(self):
        _, config = scale.prepare_cases()
        packs = scale.contexts(config)
        for variant, languages in packs.items():
            for language, pack in languages.items():
                self.assertGreaterEqual(pack['visible_tokens_estimate'], 1024)
                self.assertLessEqual(pack['visible_tokens_estimate'], 1480)
                self.assertGreaterEqual(len(core.ENCODING.encode(pack['parts'][0])), 1024)
        self.assertEqual(packs['luna_compact_terms']['zhCN']['parts'][0],
                         packs['luna_compact_examples']['ruRU']['parts'][0])

    def test_source_and_reference_metadata_have_explicit_boundaries(self):
        cases, config = scale.prepare_cases()
        body = scale.request_body('luna_compact_terms', cases[0], scale.contexts(config), 'test-run')
        user = json.loads(body['messages'][1]['content'])
        self.assertEqual(user['source_text'], cases[0]['source'])
        self.assertEqual(user['context']['mod'], cases[0]['mod_name'])
        self.assertNotIn('mod', user['source_text'].lower())

    def test_opposite_order_disagreement_is_inconclusive(self):
        rows = []
        for kind, preferred in [('comparison', 'luna_none'), ('reversed', 'original')]:
            rows.append({'id': 'sample', 'language': 'zhCN', 'variant': 'luna_none', 'kind': kind,
                         'grade': {}, 'preferred_variant': preferred,
                         'scores': {'luna_none': 90 if kind == 'comparison' else 70,
                                    'original': 70 if kind == 'comparison' else 90}})
        summary = scale.paired_summaries(rows)['luna_none']
        self.assertEqual(summary['outcomes'], {'inconclusive': 1})
        self.assertEqual(summary['mean_score_delta'], 0)

    def test_stratified_samples_are_separate_from_examples(self):
        cases, _ = scale.prepare_cases()
        self.assertEqual(len(cases), 432)
        self.assertEqual(len({c['id'] for c in cases}), 64)
        examples = json.loads((scale.ROOT / 'test/translation_style_examples.json').read_text())
        sources = {c['raw'].strip().lower() for c in cases}
        self.assertTrue(all(e['source'].strip().lower() not in sources for e in examples))
        self.assertEqual(len({c['id'] for c in cases if c['repeat_index'] == 1}), 8)
        samples = json.loads((scale.ROOT / 'test/translation_scale_samples.json').read_text())['samples']
        for sample in samples:
            if sample['id'] in ('update_008aacd83a', 'update_038cc3d2c2'):
                values = re.search(r'(0\.\d+).*?(\d+)%', sample['new_text'])
                self.assertAlmostEqual(float(values[1]) * 100, int(values[2]))

    def test_reservations_prevent_concurrent_overspend_and_release_unused_cost(self):
        budget = scale.Budget(0.01)
        self.assertTrue(budget.reserve(0.006))
        self.assertFalse(budget.reserve(0.006))
        budget.settle(0.006, 0.001)
        self.assertTrue(budget.reserve(0.006))
        budget.settle(0.006, None)
        self.assertEqual(budget.unknown, 1)
        self.assertAlmostEqual(budget.spent, 0.007)
        self.assertFalse(budget.reserve(0.004))

    def test_grader_does_not_receive_candidate_model_labels(self):
        body = scale.judge_request({'source': 'Small Tank', 'language': 'zhCN'}, '小型水箱', '小型坦克', {})
        content = json.dumps(body['messages'], ensure_ascii=False)
        self.assertNotIn('gpt-4o-mini', content)
        self.assertNotIn('luna_compact', content)
        self.assertNotIn('gpt-6-luna', content)
        self.assertEqual(body['reasoning_effort'], 'none')

    def test_hidden_order_is_mapped_back_correctly(self):
        grade = {'winner': 'A', 'A': dict.fromkeys(scale.DIMENSIONS, 5),
                 'B': dict.fromkeys(scale.DIMENSIONS, 2), 'reason': 'Correct liquid meaning'}
        row = {'labels': {'A': 'luna_compact_terms', 'B': 'original'},
               'raw_text': json.dumps(grade), 'finish_reason': 'stop'}
        scale.judge_result(row)
        self.assertEqual(row['preferred_variant'], 'luna_compact_terms')
        self.assertEqual(row['scores']['luna_compact_terms'], 100)

    def test_cluster_interval_and_locale_numeric_normalization(self):
        rows = [{'id': str(i), 'variant': 'new', 'scores': {'original': 70, 'new': 80}} for i in range(5)]
        self.assertEqual(scale.bootstrap_interval(rows), [10, 10])
        self.assertEqual(core.numbers('2,000 hp'), core.numbers('2\u202f000 hp'))
        self.assertNotIn('numbers_changed', core.format_issues('Wait for two days.', '2日間待ちます。'))
        self.assertIn('numbers_changed', core.format_issues('Wait for two days.', '3日間待ちます。'))


if __name__ == '__main__':
    unittest.main()
