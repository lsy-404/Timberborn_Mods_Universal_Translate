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
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import requests
from typing import List, Optional

from translation_prompt import (
    protected_format_issues, protected_format_signature, restore_source_boundary_whitespace,
    restore_source_line_breaks, strip_extra_quotes,
)


MAX_RETRY_AFTER_SECONDS = 30


def _retry_after_seconds(response):
    value = getattr(response, 'headers', {}).get('Retry-After')
    if value is None:
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    if delay < 0:
        return None
    return min(delay, MAX_RETRY_AFTER_SECONDS)


class TranslatorLLM:
    """Haiku localization with rate limiting and budget accounting."""

    PRICES_PER_MILLION = {
        'input': 0.10, 'cached': 0.01, 'write_5m': 0.125, 'write_1h': 0.20, 'output': 0.50,
        'long_context_threshold': 100_000,
        'long_context_input': 0.50, 'long_context_cached': 0.05,
        'long_context_write_5m': 0.625, 'long_context_write_1h': 1.0,
        'long_context_output': 2.50,
    }
    MAX_ATTEMPTS = 2

    def __init__(
        self,
        api_token: str,
        model: str = "claude-haiku-5-5",
        api_url: str = "https://llm.leisiyu.dev/v1/chat/completions",
        min_length: int = 1,
        max_length: int = 5000,
        max_output_tokens: int = 1024,
        rate_limit: str = "10/m",
        max_cost: float = 0.0,
        cost_warning_threshold: float = 1.0,
    ):
        if model != 'claude-haiku-5-5':
            raise ValueError('Production localization requires claude-haiku-5-5')
        if type(max_output_tokens) is not int or max_output_tokens < 1 or max_output_tokens > 1024:
            raise ValueError('max_output_tokens must be an integer from 1 to 1024')
        self.api_token = api_token
        self.model = model
        self.api_url = api_url
        self.min_length = min_length
        self.max_length = max_length
        self.max_output_tokens = max_output_tokens
        self.rate_limit = rate_limit
        self.request_history = []
        self._rate_limit_lock = threading.Lock()
        self._warmup_lock = threading.Lock()
        self._initial_request_completed = False
        self.logger = logging.getLogger(self.__class__.__name__)
        self._parse_rate_limit()
        self._cost_lock = threading.Lock()
        self.total_tokens = dict.fromkeys((
            "input", "ordinary", "cached", "write", "write_5m", "write_1h", "output", "reasoning", "total"
        ), 0)
        self.total_cost = 0.0
        self.reserved_cost = 0.0
        self._in_flight_requests = 0
        self.request_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.recovered_fail_count = 0
        self.unresolved_translation_count = 0
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
        is_long_context = input_bound > prices['long_context_threshold']
        input_rate = prices['long_context_write_1h'] if is_long_context else prices['write_1h']
        output_rate = prices['long_context_output'] if is_long_context else prices['output']
        maximum = (input_bound * input_rate + self.max_output_tokens * output_rate) / 1e6
        with self._cost_lock:
            if self.unknown_cost_requests or (self.max_cost > 0 and self.total_cost + self.reserved_cost + maximum > self.max_cost):
                self.logger.warning("Translation paused: insufficient budget or incomplete usage accounting")
                return None
            self.reserved_cost += maximum
            self._in_flight_requests += 1
        return maximum

    @classmethod
    def usage_metrics(cls, usage):
        if not isinstance(usage, dict):
            raise ValueError('Missing token usage')
        details = usage.get('prompt_tokens_details') or {}
        if not isinstance(details, dict):
            raise ValueError('Invalid prompt token details')
        completion_details = usage.get('completion_tokens_details') or {}
        if not isinstance(completion_details, dict):
            raise ValueError('Invalid completion token details')
        creation_details = details.get('cache_creation_token_details') or {}
        if not isinstance(creation_details, dict):
            raise ValueError('Invalid cache creation token details')
        write_5m = creation_details.get('ephemeral_5m_input_tokens')
        write_1h = creation_details.get('ephemeral_1h_input_tokens')
        write_5m = 0 if write_5m is None else write_5m
        write_1h = 0 if write_1h is None else write_1h
        write_total = details.get('cache_write_tokens')
        if write_total is None:
            write_total = details.get('cache_creation_tokens')
        if write_total is None:
            write_total = write_5m + write_1h
        else:
            known_breakdown = write_5m + write_1h
            if known_breakdown > write_total:
                raise ValueError('Inconsistent cache creation token usage')
            if known_breakdown < write_total:
                write_1h += write_total - known_breakdown
        cached = details.get('cached_tokens', 0)
        cached = 0 if cached is None else cached
        metrics = {
            'input': usage['prompt_tokens'], 'output': usage['completion_tokens'],
            'cached': cached, 'write': write_total, 'write_5m': write_5m, 'write_1h': write_1h,
            'reasoning': completion_details.get('reasoning_tokens', 0),
        }
        if any(type(value) is not int or value < 0 for value in metrics.values()):
            raise ValueError('Invalid token usage')
        metrics['ordinary'] = metrics['input'] - metrics['cached'] - metrics['write']
        if (metrics['ordinary'] < 0 or metrics['reasoning'] > metrics['output']
                or metrics['write'] != metrics['write_5m'] + metrics['write_1h']):
            raise ValueError('Inconsistent token usage')
        metrics['total'] = metrics['input'] + metrics['output']
        prices = cls.PRICES_PER_MILLION
        input_rate = prices['input']
        cached_rate = prices['cached']
        write_5m_rate = prices['write_5m']
        write_1h_rate = prices['write_1h']
        output_rate = prices['output']
        if metrics['input'] > prices['long_context_threshold']:
            input_rate = prices['long_context_input']
            cached_rate = prices['long_context_cached']
            write_5m_rate = prices['long_context_write_5m']
            write_1h_rate = prices['long_context_write_1h']
            output_rate = prices['long_context_output']
        cost = (metrics['ordinary'] * input_rate + metrics['cached'] * cached_rate
                + metrics['write_5m'] * write_5m_rate + metrics['write_1h'] * write_1h_rate
                + metrics['output'] * output_rate) / 1e6
        return metrics, cost

    def translate(self, text: str, target_language: str, system_prompt: List[dict], user_prompt: str,
                  preserve_line_entries: bool = False) -> Optional[str]:
        if not self.api_token:
            raise ValueError('API token is required')
        if not text or not text.strip():
            return text
        if not self.should_translate(text):
            with self._cost_lock:
                self.unresolved_translation_count += 1
            return None
        if len(text) < self.min_length:
            return text
        if len(text) > self.max_length:
            self.logger.error(f'Source exceeds maximum length for {target_language}; preserving the pending entry')
            with self._cost_lock:
                self.unresolved_translation_count += 1
            return None
        if not self._initial_request_completed:
            with self._warmup_lock:
                if not self._initial_request_completed:
                    result = self._request(text, system_prompt, user_prompt, preserve_line_entries)
                    self._initial_request_completed = result is not None
                    return result
        return self._request(text, system_prompt, user_prompt, preserve_line_entries)

    def _request(self, text, system_prompt, user_prompt, preserve_line_entries=False):
        """Allow one recovery attempt while accounting for every actual request."""
        feedback = None
        for attempt in range(self.MAX_ATTEMPTS):
            translated, retry_reason = self._request_once(
                text, system_prompt, user_prompt, feedback, preserve_line_entries)
            if translated is not None:
                with self._cost_lock:
                    self.recovered_fail_count += attempt
                return translated
            if (attempt + 1 == self.MAX_ATTEMPTS or retry_reason is None
                    or not self.check_cost_limit()):
                break
            self.logger.warning('Retrying translation after %s (attempt %s/%s)',
                                retry_reason, attempt + 2, self.MAX_ATTEMPTS)
            if retry_reason.startswith('HTTP 429;'):
                time.sleep(float(retry_reason.split(';', 1)[1]))
                continue
            if not retry_reason.startswith('HTTP '):
                feedback = (
                    f'The previous response failed validation: {retry_reason}. '
                    'Translate the same source_text again. Return only the complete localized string. '
                    'Use only source_text for formatting; old sources, translations, hints and examples '
                    'must not add or remove formatting. Match this source_format_contract exactly '
                    '(zero counts and empty token maps mean add none): '
                    + json.dumps(protected_format_signature(text, preserve_line_entries),
                                 ensure_ascii=False, separators=(',', ':'))
                    + '. Preserve every source placeholder and rich-text tag exactly, and retain all '
                    'source line breaks and literal backslash sequences. Keep every line entry '
                    'in order, including empty entries. Do not add commentary.'
                )
            time.sleep(1)
        with self._cost_lock:
            self.unresolved_translation_count += 1
        return None

    def _request_once(self, text, system_prompt, user_prompt, feedback=None, preserve_line_entries=False):
        """Return (translation, safe retry reason) and settle this attempt alone."""
        messages = [{'role': 'system', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
        if feedback:
            messages.append({'role': 'user', 'content': feedback})
        self._check_rate_limit()
        reservation = self._reserve_cost(messages)
        if reservation is None:
            return None, None
        data = {'model': self.model, 'max_tokens': self.max_output_tokens, 'messages': messages}
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
                retry_after = _retry_after_seconds(response) if response.status_code == 429 else None
                retry = (f'HTTP 429;{retry_after}' if retry_after is not None else
                         f'HTTP {response.status_code}' if response.status_code in (500, 502, 503, 504) else None)
                return None, retry
            payload = response.json()
            metrics, actual_cost = self.usage_metrics(payload['usage'])
            returned_model = payload.get('model', self.model)
            with self._cost_lock:
                self.returned_models.add(returned_model)
            choice = payload['choices'][0]
            finish_reason = choice.get('finish_reason', 'stop')
            if not returned_model.startswith(self.model) or metrics['reasoning'] != 0 or finish_reason != 'stop':
                self.logger.error(f'Rejected completion: model={returned_model}, reasoning={metrics["reasoning"]}, finish={finish_reason}')
                return None, None
            translated = choice['message']['content']
            if not isinstance(translated, str) or not translated.strip():
                self.logger.error('Rejected empty or malformed completion')
                return None, 'empty_completion'
            translated = strip_extra_quotes(translated.strip(' \t\r\n'), text)
            if (not translated.strip()
                    or (text.strip(' \t\r\n"') and not translated.strip(' \t\r\n"'))):
                self.logger.error('Rejected empty completion after removing quote wrappers')
                return None, 'empty_completion'
            translated = restore_source_line_breaks(text, translated)
            translated = restore_source_boundary_whitespace(text, translated)
            translated = restore_source_line_breaks(text, translated)
            issues = protected_format_issues(text, translated, preserve_line_entries)
            if issues:
                self.logger.error(f'Rejected protected formatting changes: {issues}')
                return None, ', '.join(issues)
            succeeded = True
            return translated, None
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
            self.logger.error(f'Translation request failed: {str(error).replace(self.api_token, "[redacted]")}')
            return None, None
        finally:
            with self._cost_lock:
                self._in_flight_requests -= 1
                self.reserved_cost = max(0.0, self.reserved_cost - reservation) if self._in_flight_requests else 0.0
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
                'model': self.model,
                'returned_models': sorted(self.returned_models),
                'request_count': self.request_count, 'success_count': self.success_count, 'fail_count': self.fail_count,
                'recovered_fail_count': self.recovered_fail_count,
                'unresolved_translation_count': self.unresolved_translation_count,
                'success_rate': self.success_count / max(1, self.request_count) * 100,
                'input_tokens': self.total_tokens['input'], 'ordinary_input_tokens': self.total_tokens['ordinary'],
                'cached_tokens': self.total_tokens['cached'], 'cache_write_tokens': self.total_tokens['write'],
                'cache_write_5m_tokens': self.total_tokens['write_5m'],
                'cache_write_1h_tokens': self.total_tokens['write_1h'],
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
