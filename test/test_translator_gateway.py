from pathlib import Path
import json
import sys
import threading
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts" / "util"))
from translator import TranslatorLLM
from gateway_get_stub import gateway_get_stub
import requests


class GatewayTranslationTests(unittest.TestCase):
    def setUp(self):
        self.usage = 100
        def gateway_get(url, **kwargs):
            payload = ({'success': True, 'data': {'quota_per_unit': 1000000}}
                       if url.endswith('/api/status') else
                       {'code': True, 'data': {'object': 'token_usage', 'total_used': self.usage}})
            return Mock(json=Mock(return_value=payload))
        get = patch('translator.requests.get', side_effect=gateway_get_stub(gateway_get, requests.get))
        get.start()
        self.addCleanup(get.stop)

    def test_config_bounds_gateway_model_rate_and_concurrency(self):
        with (ROOT / ".github" / "config" / "config.toml").open("rb") as stream:
            config = tomllib.load(stream)
        self.assertEqual(config["llm"]["model"], "pearl")
        self.assertEqual(config["llm"]["api_url"], "https://llm.leisiyu.dev/v1/chat/completions")
        self.assertEqual(config["llm"]["max_output_tokens"], 1024)
        self.assertEqual(config["rate_limiter"]["max_requests_per_minute"], 600)
        self.assertEqual(config["rate_limiter"]["max_threads"], 2)
        self.assertEqual(config["rate_limiter"]["max_threads_per_file"], 2)
        workflow = (ROOT / ".github" / "workflows" / "translate.yml").read_text(encoding="utf-8")
        self.assertIn("group: tmut-translation", workflow)
        self.assertIn("cancel-in-progress: false", workflow)

    @patch("translator.requests.post")
    def test_request_uses_gateway_model_and_output_cap(self, post):
        post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "model": "pearl", "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 50, "total_tokens": 1250},
            },
        )
        translator = TranslatorLLM("test-token", model="pearl", rate_limit="")
        result = translator.translate("source", "frFR", "system", "user")
        self.assertEqual(result, "translated")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://llm.leisiyu.dev/v1/chat/completions")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "pearl")
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertEqual(translator.get_cost_summary_dict()['key_usage_delta_usd'], 0)

    @patch("translator.time.sleep")
    @patch("translator.requests.post")
    def test_job_session_is_shared_by_workers_and_retries(self, post, sleep):
        response = lambda content: SimpleNamespace(status_code=200, json=lambda: {
            "model": "pearl", "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 2},
        })
        post.side_effect = [response(""), response("translated"), response("translated"), response("translated")]
        translator = TranslatorLLM("test-token", rate_limit="")
        other_job = TranslatorLLM("test-token", rate_limit="")
        self.assertNotEqual(translator.session_id, other_job.session_id)
        self.assertEqual(str(UUID(translator.session_id)), translator.session_id)
        self.assertEqual(translator.translate("warmup", "frFR", [], "warmup"), "translated")
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda pair: translator.translate(pair[0], pair[1], [], pair[0]),
                [("file one", "frFR"), ("file two", "deDE")],
            ))
        self.assertEqual(results, ["translated", "translated"])
        self.assertEqual(post.call_count, 4)
        self.assertEqual({call.kwargs['headers']['session-id'] for call in post.call_args_list},
                         {translator.session_id})
        self.assertEqual(translator.get_cost_summary_dict()['session_id'], translator.session_id)

    @patch("translator.requests.post")
    def test_large_context_uses_gateway_counter(self, post):
        def respond(*args, **kwargs):
            self.usage += 1234
            return SimpleNamespace(status_code=200, json=lambda: {
                "model": "pearl", "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 100001, "completion_tokens": 1000},
            })
        post.side_effect = respond
        translator = TranslatorLLM("test-token", rate_limit="")
        self.assertEqual(translator.translate("source", "frFR", "system", "user"), "translated")
        self.assertAlmostEqual(translator.get_cost_summary_dict()['key_usage_delta_usd'], 0.001234)

    @patch("translator.requests.post")
    def test_429_does_not_retry_when_charge_is_uncertain(self, post):
        post.return_value = SimpleNamespace(status_code=429, headers={"Retry-After": "60"}, text="limited")
        translator = TranslatorLLM("test-token", rate_limit="")
        self.assertIsNone(translator.translate("source", "frFR", "system", "user"))
        post.assert_called_once()
        self.assertFalse(translator.get_cost_summary_dict()['cost_tracking_complete'])

    def test_output_limit_must_be_positive(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TranslatorLLM("test-token", max_output_tokens=value)

    @patch("translator.requests.post")
    def test_requests_run_concurrently_after_uncached_initial_success(self, post):
        simultaneous_requests = threading.Barrier(2)
        initial_call_finished = threading.Event()

        def accepted_response(*args, **kwargs):
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "model": "pearl",
                    "choices": [{"message": {"content": "translated"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 2, "total_tokens": 14},
                },
            )

        def request(*args, **kwargs):
            if initial_call_finished.is_set():
                simultaneous_requests.wait(timeout=3)
            return accepted_response()

        post.side_effect = request
        translator = TranslatorLLM("test-token", rate_limit="")
        self.assertEqual(translator.translate("warmup", "frFR", [], "warmup"), "translated")
        self.assertTrue(translator._initial_request_completed)
        initial_call_finished.set()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                lambda text: translator.translate(text, "frFR", [], text),
                ("first", "second"),
            ))
        self.assertEqual(results, ["translated", "translated"])
        self.assertEqual(post.call_count, 3)


if __name__ == "__main__":
    unittest.main()
