# SPDX-License-Identifier: (ALE-1.1 AND GPL-3.0-only)
# Copyright (c) 2022-2025 wuyilingwei
#
# This file is licensed under the ANTI-LABOR EXPLOITATION LICENSE 1.1
# in combination with GNU General Public License v3.0.
# See .github/LICENSE for full license text.
"""
Translator module for Timberborn mods translation
Adapted from https://github.com/wuyilingwei/Timberborn_Tools
"""
import time
import json
import logging
import threading
import requests
from typing import List, Optional

from translation_prompt import protected_format_issues, restore_source_line_breaks


class TranslatorLLM:
    """Luna localization with rate limiting and cache-aware budget accounting."""

    PRICES_PER_MILLION = {"input": 0.10, "cached": 0.01, "write": 0.125, "output": 0.50}
    MAX_COMPLETION_TOKENS = 8192

    def __init__(
        self,
        api_token: str,
        model: str = "gpt-6-luna",
        api_url: str = "https://api.openai.com/v1/chat/completions",
        min_length: int = 1,
        max_length: int = 5000,
        rate_limit: str = "10/m",
        max_cost: float = 0.0,
        cost_warning_threshold: float = 1.0,
        reasoning_effort: str = "none",
    ):
        if model != "gpt-6-luna" or reasoning_effort != "none":
            raise ValueError("Production localization requires gpt-6-luna with reasoning_effort=none")
        self.api_token = api_token
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.api_url = api_url
        self.min_length = min_length
        self.max_length = max_length
        self.rate_limit = rate_limit
        self.request_history = []
        self._rate_limit_lock = threading.Lock()
        self._warmup_lock = threading.Lock()
        self._cache_warmed = False
        self.logger = logging.getLogger(self.__class__.__name__)
        self._parse_rate_limit()
        self._cost_lock = threading.Lock()
        self.total_tokens = dict.fromkeys(("input", "ordinary", "cached", "write", "output", "reasoning", "total"), 0)
        self.total_cost = 0.0
        self.reserved_cost = 0.0
        self.request_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.unknown_cost_requests = 0
        self.returned_models = set()
        self.max_cost = max_cost
        self.cost_warning_threshold = cost_warning_threshold
        self._warning_shown = False

    def _parse_rate_limit(self) -> None:
        if not self.rate_limit:
            self.rate_limit_num = None
            self.rate_limit_seconds = None
            return
        num, unit = self.rate_limit.split('/')
        self.rate_limit_num = int(num)
        self.rate_limit_seconds = {'s': 1, 'm': 60, 'h': 3600}[unit]

    def _check_rate_limit(self) -> None:
        if not self.rate_limit_num:
            return
        while True:
            with self._rate_limit_lock:
                current_time = time.time()
                self.request_history = [t for t in self.request_history if current_time - t < self.rate_limit_seconds]
                if len(self.request_history) < self.rate_limit_num:
                    self.request_history.append(current_time)
                    return
                delay = self.rate_limit_seconds - (current_time - self.request_history[0]) + 0.1
            time.sleep(max(0.05, delay))

    def _reserve_cost(self, messages):
        # UTF-8 bytes bound the text token count without an extra runtime dependency.
        input_bound = len(json.dumps(messages, ensure_ascii=False).encode('utf-8')) + 128
        prices = self.PRICES_PER_MILLION
        maximum = (input_bound * max(prices['input'], prices['write'])
                   + self.MAX_COMPLETION_TOKENS * prices['output']) / 1e6
        with self._cost_lock:
            if self.unknown_cost_requests or (self.max_cost > 0 and self.total_cost + self.reserved_cost + maximum > self.max_cost):
                self.logger.warning("Translation paused: insufficient budget or incomplete usage accounting")
                return None
            self.reserved_cost += maximum
        return maximum

    @classmethod
    def usage_metrics(cls, usage):
        details = usage['prompt_tokens_details']
        metrics = {
            'input': usage['prompt_tokens'], 'output': usage['completion_tokens'],
            'cached': details['cached_tokens'], 'write': details['cache_write_tokens'],
            'reasoning': usage['completion_tokens_details']['reasoning_tokens'],
        }
        if any(type(value) is not int or value < 0 for value in metrics.values()):
            raise ValueError('Invalid token usage')
        metrics['ordinary'] = metrics['input'] - metrics['cached'] - metrics['write']
        if metrics['ordinary'] < 0 or metrics['reasoning'] > metrics['output']:
            raise ValueError('Inconsistent token usage')
        metrics['total'] = metrics['input'] + metrics['output']
        prices = cls.PRICES_PER_MILLION
        cost = (metrics['ordinary'] * prices['input'] + metrics['cached'] * prices['cached']
                + metrics['write'] * prices['write'] + metrics['output'] * prices['output']) / 1e6
        return metrics, cost

    def translate(self, text: str, target_language: str, system_prompt: List[dict], user_prompt: str) -> Optional[str]:
        if not self.api_token:
            raise ValueError('API token is required')
        if not text or not text.strip():
            return text
        if not self.should_translate(text):
            return None
        if len(text) < self.min_length:
            return text
        if len(text) > self.max_length:
            self.logger.error(f'Source exceeds maximum length for {target_language}; preserving the pending entry')
            return None
        if not self._cache_warmed:
            with self._warmup_lock:
                if not self._cache_warmed:
                    result = self._request(text, system_prompt, user_prompt)
                    self._cache_warmed = bool(self.total_tokens['cached'] or self.total_tokens['write'])
                    return result
        return self._request(text, system_prompt, user_prompt)

    def _request(self, text, system_prompt, user_prompt):
        messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
        self._check_rate_limit()
        reservation = self._reserve_cost(messages)
        if reservation is None:
            return None
        data = {
            'model': self.model, 'reasoning_effort': self.reasoning_effort,
            'service_tier': 'default', 'max_completion_tokens': self.MAX_COMPLETION_TOKENS,
            'prompt_cache_options': {'mode': 'explicit', 'ttl': '30m'}, 'messages': messages,
        }
        actual_cost = None
        metrics = None
        succeeded = False
        with self._cost_lock:
            self.request_count += 1
        try:
            response = requests.post(
                self.api_url,
                headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_token}'},
                json=data, timeout=(10, 120),
            )
            if response.status_code != 200:
                actual_cost = 0.0
                self.logger.error(f'Translation failed: HTTP {response.status_code}: {response.text.replace(self.api_token, "[redacted]")}')
                return None
            payload = response.json()
            metrics, actual_cost = self.usage_metrics(payload['usage'])
            returned_model = payload['model']
            with self._cost_lock:
                self.returned_models.add(returned_model)
            choice = payload['choices'][0]
            if not returned_model.startswith(self.model) or metrics['reasoning'] != 0 or choice['finish_reason'] != 'stop':
                self.logger.error(f'Rejected completion: model={returned_model}, reasoning={metrics["reasoning"]}, finish={choice["finish_reason"]}')
                return None
            translated = choice['message']['content']
            if not isinstance(translated, str) or not translated.strip():
                self.logger.error('Rejected empty or malformed completion')
                return None
            translated = restore_source_line_breaks(text, translated.strip(' \t'))
            issues = protected_format_issues(text, translated)
            if issues:
                self.logger.error(f'Rejected protected formatting changes: {issues}')
                return None
            succeeded = True
            return translated
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
            self.logger.error(f'Translation request failed: {str(error).replace(self.api_token, "[redacted]")}')
            return None
        finally:
            with self._cost_lock:
                self.reserved_cost = max(0.0, self.reserved_cost - reservation)
                self.total_cost += reservation if actual_cost is None else actual_cost
                self.unknown_cost_requests += actual_cost is None
                if metrics is not None:
                    for key, value in metrics.items():
                        self.total_tokens[key] += value
                self.success_count += succeeded
                self.fail_count += not succeeded
                if not self._warning_shown and self.total_cost > self.cost_warning_threshold:
                    self.logger.warning(f'Cost warning: current ${self.total_cost:.6f} exceeds ${self.cost_warning_threshold:.2f}')
                    self._warning_shown = True
                if actual_cost is None:
                    self.logger.error('Usage accounting incomplete; reserved the maximum cost and stopped new requests')
            if metrics is not None:
                self.logger.info(f'Token usage: input={metrics["input"]}, cached={metrics["cached"]}, write={metrics["write"]}, output={metrics["output"]}, reasoning={metrics["reasoning"]}, cost=${actual_cost:.8f}')

    def get_cost_summary_dict(self) -> dict:
        with self._cost_lock:
            return {
                'model': self.model, 'reasoning_effort': self.reasoning_effort,
                'returned_models': sorted(self.returned_models),
                'request_count': self.request_count, 'success_count': self.success_count, 'fail_count': self.fail_count,
                'success_rate': self.success_count / max(1, self.request_count) * 100,
                'input_tokens': self.total_tokens['input'], 'ordinary_input_tokens': self.total_tokens['ordinary'],
                'cached_tokens': self.total_tokens['cached'], 'cache_write_tokens': self.total_tokens['write'],
                'output_tokens': self.total_tokens['output'], 'reasoning_tokens': self.total_tokens['reasoning'],
                'total_tokens': self.total_tokens['total'],
                'cache_hit_rate': self.total_tokens['cached'] / max(1, self.total_tokens['input']),
                'estimated_cost_usd': self.total_cost, 'reserved_cost_usd': self.reserved_cost,
                'unknown_cost_requests': self.unknown_cost_requests,
                'cost_tracking_complete': self.unknown_cost_requests == 0,
            }

    def get_cost_summary(self) -> str:
        return '\nTRANSLATION COST SUMMARY\n' + json.dumps(self.get_cost_summary_dict(), indent=2)

    def check_cost_limit(self) -> bool:
        with self._cost_lock:
            return not self.unknown_cost_requests and (self.max_cost <= 0 or self.total_cost + self.reserved_cost < self.max_cost)

    def should_translate(self, text: str) -> bool:
        return bool(text and text.strip()) and self.check_cost_limit()
