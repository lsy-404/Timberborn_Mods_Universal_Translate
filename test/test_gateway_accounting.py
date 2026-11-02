import sys
import unittest
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / '.github/scripts/util'))
from translator import TranslatorLLM


def http(payload):
    response = Mock(status_code=200)
    response.json.return_value = payload
    return response


def completion(model='pearl'):
    return http({'model': model, 'choices': [{'message': {'content': 'traduit'}, 'finish_reason': 'stop'}],
                 'usage': {'prompt_tokens': 100, 'completion_tokens': 10}})


class GatewayAccountingTests(unittest.TestCase):
    def setup_gateway(self, values):
        values = iter(values)
        def get(url, **kwargs):
            if url.endswith('/api/status'):
                return http({'success': True, 'data': {'quota_per_unit': 1000}})
            value = next(values)
            if isinstance(value, Exception):
                raise value
            return http({'code': True, 'data': {'object': 'token_usage', 'total_used': value}})
        return get

    def test_status_and_usage_require_their_distinct_envelopes(self):
        status = {'success': True, 'data': {'quota_per_unit': 1000}}
        usage = {'code': True, 'data': {'object': 'token_usage', 'total_used': 25}}
        for bad_status, bad_usage in [
            ({'code': True, 'data': {'quota_per_unit': 1000}}, usage),
            (status, {'success': True, 'data': {'object': 'token_usage', 'total_used': 25}}),
            (status, {'code': False, 'data': {'object': 'token_usage', 'total_used': 25}}),
            (status, {'code': True, 'data': {'object': 'other', 'total_used': 25}}),
            (status, {'code': True, 'data': {'object': 'token_usage', 'total_used': True}}),
            (status, {'code': True, 'data': {'object': 'token_usage', 'total_used': -1}}),
        ]:
            with self.subTest(status=bad_status, usage=bad_usage):
                client = TranslatorLLM('token', rate_limit='')
                with patch('translator.requests.get', side_effect=[http(bad_status), http(bad_usage)]):
                    self.assertFalse(client._refresh_usage())
                    self.assertFalse(client.check_cost_limit())
        client = TranslatorLLM('token', rate_limit='')
        with patch('translator.requests.get', side_effect=[http(status), http(usage)]):
            self.assertTrue(client._refresh_usage())
            self.assertEqual(client._baseline_usage, 25)

    @patch('translator.requests.post')
    @patch('translator.requests.get')
    def test_settled_key_usage_and_threshold(self, get, post):
        get.side_effect = self.setup_gateway([100, 200, 200])
        post.return_value = completion()
        client = TranslatorLLM('token', rate_limit='', max_cost=.05)
        self.assertEqual(client.translate('source', 'fr', [], 'source'), 'traduit')
        client._last_usage_refresh -= client.USAGE_REFRESH_INTERVAL_SECONDS
        self.assertIsNone(client.translate('next', 'fr', [], 'next'))
        report = client.get_cost_summary_dict()
        self.assertAlmostEqual(report['key_usage_delta_usd'], .1)
        self.assertTrue(report['cost_tracking_complete'])
        post.assert_called_once()

    @patch('translator.requests.post')
    @patch('translator.requests.get')
    def test_usage_failure_stops_requests(self, get, post):
        get.side_effect = self.setup_gateway([ValueError('bad counter')])
        client = TranslatorLLM('token', rate_limit='')
        self.assertIsNone(client.translate('source', 'fr', [], 'source'))
        self.assertFalse(client.get_cost_summary_dict()['cost_tracking_complete'])
        post.assert_not_called()

    @patch('translator.requests.post')
    @patch('translator.requests.get')
    def test_bad_response_model_is_rejected_but_cost_settles(self, get, post):
        get.side_effect = self.setup_gateway([10, 12, 12])
        post.return_value = completion('different')
        client = TranslatorLLM('token', rate_limit='')
        self.assertIsNone(client.translate('source', 'fr', [], 'source'))
        report = client.get_cost_summary_dict()
        self.assertAlmostEqual(report['key_usage_delta_usd'], .002)
        self.assertTrue(report['cost_tracking_complete'])

    @patch('translator.requests.post')
    @patch('translator.requests.get')
    def test_request_failure_is_unconfirmed(self, get, post):
        import requests
        get.side_effect = self.setup_gateway([100, 100, 100])
        post.side_effect = requests.Timeout('timeout')
        client = TranslatorLLM('token', rate_limit='')
        self.assertIsNone(client.translate('source', 'fr', [], 'source'))
        self.assertFalse(client.get_cost_summary_dict()['cost_tracking_complete'])
        self.assertFalse(client.check_cost_limit())

    def test_counter_decrease_outside_inflight_is_rejected(self):
        client = TranslatorLLM('token', rate_limit='')
        with patch('translator.requests.get', side_effect=self.setup_gateway([100, 120, 110])):
            self.assertTrue(client._refresh_usage())
            self.assertTrue(client._refresh_usage())
            self.assertFalse(client._refresh_usage(final=True))

    def test_alternate_provider_usage_without_cache_details(self):
        metrics, cost = TranslatorLLM.usage_metrics({'prompt_tokens': 20, 'completion_tokens': 4,
            'completion_tokens_details': {'reasoning_tokens': 2}})
        self.assertEqual((metrics['ordinary'], metrics['reasoning']), (20, 2))
        self.assertIsNone(cost)
        metrics, _ = TranslatorLLM.usage_metrics({'prompt_tokens': 20, 'completion_tokens': 4,
            'completion_tokens_details': {'reasoning_tokens': None}})
        self.assertEqual(metrics['reasoning'], 0)

    @patch('translator.requests.post')
    @patch('translator.requests.get')
    def test_two_concurrent_requests_settle_to_final_counter(self, get, post):
        get.side_effect = self.setup_gateway([100, 120])
        barrier = threading.Barrier(2)
        def request(*args, **kwargs):
            barrier.wait(timeout=3)
            return completion()
        post.side_effect = request
        client = TranslatorLLM('token', rate_limit='', max_cost=1)
        client._initial_request_completed = True
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda text: client.translate(text, 'fr', [], text), ['one', 'two']))
        self.assertEqual(results, ['traduit', 'traduit'])
        self.assertAlmostEqual(client.get_cost_summary_dict()['key_usage_delta_usd'], .02)
        self.assertEqual(get.call_count, 3)

    @patch('translator.requests.post', return_value=completion())
    @patch('translator.requests.get')
    def test_final_refresh_is_cached_until_another_request(self, get, post):
        get.side_effect = self.setup_gateway([100, 120, 130])
        client = TranslatorLLM('token', rate_limit='')
        self.assertEqual(client.translate('one', 'fr', [], 'one'), 'traduit')
        self.assertAlmostEqual(client.get_cost_summary_dict()['key_usage_delta_usd'], .02)
        self.assertAlmostEqual(client.get_cost_summary_dict()['key_usage_delta_usd'], .02)
        self.assertEqual(get.call_count, 3)
        self.assertEqual(client.translate('two', 'fr', [], 'two'), 'traduit')
        self.assertAlmostEqual(client.get_cost_summary_dict()['key_usage_delta_usd'], .03)
        self.assertEqual(get.call_count, 4)

    @patch('translator.requests.post', return_value=completion())
    @patch('translator.requests.get')
    def test_final_read_failure_does_not_confirm_stale_snapshot(self, get, post):
        get.side_effect = self.setup_gateway([100, ValueError('unavailable')])
        client = TranslatorLLM('token', rate_limit='')
        self.assertEqual(client.translate('one', 'fr', [], 'one'), 'traduit')
        report = client.get_cost_summary_dict()
        self.assertFalse(report['cost_tracking_complete'])
        self.assertEqual(report['key_usage_delta_usd'], 0)
        self.assertFalse(client.get_cost_summary_dict()['cost_tracking_complete'])
        self.assertEqual(get.call_count, 3)

    def test_usage_429_retries_only_with_short_retry_after(self):
        import requests
        for header, expected_calls in [('2', 2), ('1200', 1), ('NaN', 1), (None, 1)]:
            with self.subTest(header=header):
                limited = Mock(status_code=429, headers={} if header is None else {'Retry-After': header})
                limited.raise_for_status.side_effect = requests.HTTPError('limited')
                good = http({'code': True, 'data': {'object': 'token_usage', 'total_used': 1}})
                client = TranslatorLLM('token', rate_limit='')
                with patch('translator.requests.get', side_effect=[limited, good]) as get, patch('translator.time.sleep') as sleep:
                    if expected_calls == 2:
                        self.assertEqual(client._read_usage(), 1)
                        sleep.assert_called_once_with(2)
                    else:
                        with self.assertRaises(requests.HTTPError):
                            client._read_usage()
                        sleep.assert_not_called()
                    self.assertEqual(get.call_count, expected_calls)

    def test_transient_reservations_can_settle_lower(self):
        client = TranslatorLLM('token', rate_limit='')
        with patch('translator.requests.get', side_effect=self.setup_gateway([100, 160, 120])):
            self.assertTrue(client._refresh_usage())
            client._in_flight_requests = 2
            self.assertTrue(client._refresh_usage())
            client._in_flight_requests = 0
            self.assertTrue(client._refresh_usage(final=True))
            self.assertAlmostEqual(client.total_cost, .02)

if __name__ == '__main__':
    unittest.main()
