from pathlib import Path
import json
import sys
import threading
import tomllib
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".github" / "scripts" / "util"))
from translator import TranslatorLLM


class GatewayTranslationTests(unittest.TestCase):
    def test_config_bounds_gateway_model_rate_and_concurrency(self):
        with (ROOT / ".github" / "config" / "config.toml").open("rb") as stream:
            config = tomllib.load(stream)
        self.assertEqual(config["llm"]["model"], "claude-haiku-5-5")
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
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 1200, "completion_tokens": 50, "total_tokens": 1250},
            },
        )
        translator = TranslatorLLM("test-token", model="claude-haiku-5-5", rate_limit="")
        result = translator.translate("source", "frFR", "system", "user")
        self.assertEqual(result, "translated")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://llm.leisiyu.dev/v1/chat/completions")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "claude-haiku-5-5")
        self.assertEqual(payload["max_tokens"], 1024)
        self.assertAlmostEqual(translator.total_cost, 0.000145)

    @patch("translator.requests.post")
    def test_exact_100k_context_uses_standard_price(self, post):
        post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 100_000, "completion_tokens": 0, "total_tokens": 100_000},
            },
        )
        translator = TranslatorLLM("test-token", model="claude-haiku-5-5", rate_limit="")
        translator.translate("source", "frFR", "system", "user")
        self.assertAlmostEqual(translator.total_cost, 0.01)

    @patch("translator.requests.post")
    def test_long_context_uses_haiku_long_context_prices(self, post):
        post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "choices": [{"message": {"content": "translated"}}],
                "usage": {"prompt_tokens": 100_001, "completion_tokens": 1_000, "total_tokens": 101_001},
            },
        )
        translator = TranslatorLLM("test-token", model="claude-haiku-5-5", rate_limit="")
        translator.translate("source", "frFR", "system", "user")
        self.assertAlmostEqual(translator.total_cost, 0.0525005)

    @patch("translator.time.sleep")
    @patch("translator.requests.post")
    def test_429_retry_respects_a_bounded_retry_after(self, post, sleep):
        throttled = SimpleNamespace(status_code=429, headers={"Retry-After": "60"}, text="limited")
        accepted = SimpleNamespace(
            status_code=200,
            json=lambda: {"choices": [{"message": {"content": "translated"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}},
        )
        post.side_effect = [throttled, accepted]
        translator = TranslatorLLM("test-token", model="claude-haiku-5-5", rate_limit="")
        self.assertEqual(translator.translate("source", "frFR", "system", "user"), "translated")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(sleep.call_args_list, [call(30)])
        self.assertEqual(translator.request_count, 2)

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
                    "model": "claude-haiku-5-5",
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
