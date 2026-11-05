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
import math
from uuid import uuid4
from urllib.parse import urlsplit
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import requests
from typing import List, Optional

from translation_prompt import (
    missing_required_terms, protected_format_issues, protected_format_signature, restore_source_boundary_whitespace,
    restore_source_line_breaks, strip_extra_quotes,
)


MAX_RETRY_AFTER_SECONDS = 30


def _retry_after_seconds(response, clamp=True):
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
    if not math.isfinite(delay) or delay < 0:
        return None
    if delay > MAX_RETRY_AFTER_SECONDS and not clamp:
        return None
    return min(delay, MAX_RETRY_AFTER_SECONDS)


class TranslatorLLM:
    """Localization with rate limiting and budget accounting."""

    MAX_ATTEMPTS = 2
    USAGE_REFRESH_INTERVAL_SECONDS = 120

    def __init__(
        self,
        api_token: str,
        model: str = "pearl",
        api_url: str = "https://llm.leisiyu.dev/v1/chat/completions",
        min_length: int = 1,
        max_length: int = 5000,
        max_output_tokens: int = 1024,
        rate_limit: str = "10/m",
        max_cost: float = 0.0,
        cost_warning_threshold: float = 1.0,
    ):
        if type(max_output_tokens) is not int or max_output_tokens < 1 or max_output_tokens > 1024:
            raise ValueError('max_output_tokens must be an integer from 1 to 1024')
        self.api_token = api_token
        self.session_id = str(uuid4())
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
        self._cost_lock = threading.RLock()
        self._quota_per_unit = None
        self._baseline_usage = None
        self._last_usage = None
        self._last_usage_refresh = None
        self._finalized_request_count = None
        self._final_attempted_request_count = None
        self._last_read_in_flight = False
        self._accounting_failed = False
        parts = urlsplit(api_url)
        self._gateway_origin = f"{parts.scheme}://{parts.netloc}"
        self.total_tokens = dict.fromkeys((
            "input", "ordinary", "cached", "write", "write_5m", "write_1h", "output", "reasoning", "total"
        ), 0)
        self.total_cost = 0.0
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

    def _get_usage_response(self, url, **kwargs):
        response = requests.get(url, **kwargs)
        if response.status_code == 429:
            delay = _retry_after_seconds(response, clamp=False)
            if delay is not None:
                time.sleep(delay)
                response = requests.get(url, **kwargs)
        response.raise_for_status()
        return response

    def _read_usage(self):
        response = self._get_usage_response(
            self._gateway_origin + '/api/usage/token/',
            headers={'Authorization': f'Bearer {self.api_token}'}, timeout=(10, 30))
        payload = response.json()
        value = payload['data']['total_used']
        if payload.get('code') is not True or payload['data'].get('object') != 'token_usage' or type(value) is not int or value < 0:
            raise ValueError('Invalid gateway usage')
        return value

    def _refresh_usage(self, final=False):
        with self._cost_lock:
            try:
                if self._quota_per_unit is None:
                    response = self._get_usage_response(self._gateway_origin + '/api/status', timeout=(10, 30))
                    payload = response.json()
                    unit = payload['data']['quota_per_unit']
                    if payload.get('success') is not True or isinstance(unit, bool) or not isinstance(unit, (int, float)) or not math.isfinite(unit) or unit <= 0:
                        raise ValueError('Invalid gateway quota unit')
                    self._quota_per_unit = unit
                current = self._read_usage()
                if self._baseline_usage is None:
                    self._baseline_usage = current
                if current < self._baseline_usage or (self._last_usage is not None and current < self._last_usage and (not self._last_read_in_flight and not self._in_flight_requests)):
                    raise ValueError('Gateway usage decreased unexpectedly')
                self._last_usage = current
                self._last_read_in_flight = self._in_flight_requests > 0
                self.total_cost = (current - self._baseline_usage) / self._quota_per_unit
                self._last_usage_refresh = time.monotonic()
                return True
            except (requests.RequestException, KeyError, TypeError, ValueError, OverflowError) as error:
                self.logger.error('Gateway usage unavailable: %s', str(error).replace(self.api_token, '[redacted]'))
                self._accounting_failed = True
                return False

    def _reserve_cost(self, messages):
        with self._cost_lock:
            refresh_due = (self._last_usage_refresh is None or
                           time.monotonic() - self._last_usage_refresh >= self.USAGE_REFRESH_INTERVAL_SECONDS)
            if self._accounting_failed or (refresh_due and not self._refresh_usage()) or (self.max_cost > 0 and self.total_cost >= self.max_cost):
                self.logger.warning('Translation paused: budget threshold or incomplete usage accounting')
                return None
            self._in_flight_requests += 1
            self._finalized_request_count = None
            return 0.0

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
        reasoning = completion_details.get('reasoning_tokens')
        reasoning = 0 if reasoning is None else reasoning
        metrics = {
            'input': usage['prompt_tokens'], 'output': usage['completion_tokens'],
            'cached': cached, 'write': write_total, 'write_5m': write_5m, 'write_1h': write_1h,
            'reasoning': reasoning,
        }
        if any(type(value) is not int or value < 0 for value in metrics.values()):
            raise ValueError('Invalid token usage')
        metrics['ordinary'] = metrics['input'] - metrics['cached'] - metrics['write']
        if (metrics['ordinary'] < 0 or metrics['reasoning'] > metrics['output']
                or metrics['write'] != metrics['write_5m'] + metrics['write_1h']):
            raise ValueError('Inconsistent token usage')
        metrics['total'] = metrics['input'] + metrics['output']
        return metrics, None

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
                    result = self._request(text, target_language, system_prompt, user_prompt, preserve_line_entries)
                    self._initial_request_completed = result is not None
                    return result
        return self._request(text, target_language, system_prompt, user_prompt, preserve_line_entries)

    def _request(self, text, target_language, system_prompt, user_prompt, preserve_line_entries=False):
        """Allow one recovery attempt while accounting for every actual request."""
        feedback = None
        for attempt in range(self.MAX_ATTEMPTS):
            translated, retry_reason = self._request_once(
                text, target_language, system_prompt, user_prompt, feedback, preserve_line_entries)
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

    def _request_once(self, text, target_language, system_prompt, user_prompt, feedback=None, preserve_line_entries=False):
        """Return (translation, safe retry reason) and settle this attempt alone."""
        messages = [{'role': 'developer', 'content': system_prompt}, {'role': 'user', 'content': user_prompt}]
        if feedback:
            messages.append({'role': 'user', 'content': feedback})
        self._check_rate_limit()
        reservation = self._reserve_cost(messages)
        if reservation is None:
            return None, None
        data = {'model': self.model, 'max_tokens': self.max_output_tokens, 'messages': messages,
                'prompt_cache_options': {'mode': 'explicit'}}
        metrics = None
        request_uncertain = False
        succeeded = False
        with self._cost_lock:
            self.request_count += 1
        try:
            response = requests.post(
                self.api_url,
                headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_token}',
                         'session-id': self.session_id},
                json=data, timeout=(10, 120),
            )
            if response.status_code != 200:
                request_uncertain = True
                self.logger.error(f'Translation failed: HTTP {response.status_code}: {response.text.replace(self.api_token, "[redacted]")}')
                retry_after = _retry_after_seconds(response) if response.status_code == 429 else None
                retry = (f'HTTP 429;{retry_after}' if retry_after is not None else
                         f'HTTP {response.status_code}' if response.status_code in (500, 502, 503, 504) else None)
                return None, retry
            payload = response.json()
            metrics, _ = self.usage_metrics(payload['usage'])
            returned_model = payload['model']
            with self._cost_lock:
                self.returned_models.add(returned_model)
            choice = payload['choices'][0]
            finish_reason = choice.get('finish_reason', 'stop')
            if returned_model != self.model or finish_reason != 'stop':
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
            missing = missing_required_terms(text, translated, target_language)
            if missing:
                reason = 'missing required terms: ' + ', '.join(
                    f'{source} -> {stem}' for source, stem in missing)
                self.logger.error('Rejected translation: %s', reason)
                return None, reason
            succeeded = True
            return translated, None
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as error:
            request_uncertain = True
            self.logger.error(f'Translation request failed: {str(error).replace(self.api_token, "[redacted]")}')
            return None, None
        finally:
            with self._cost_lock:
                if request_uncertain:
                    self._accounting_failed = True
                    self.unknown_cost_requests += 1
                self._in_flight_requests -= 1
                if metrics is not None:
                    for key, value in metrics.items():
                        self.total_tokens[key] += value
                self.success_count += succeeded
                self.fail_count += not succeeded
                if not self._warning_shown and self.total_cost > self.cost_warning_threshold:
                    self.logger.warning('Usage threshold reached: current key usage delta $%.6f', self.total_cost)
                    self._warning_shown = True

    def get_cost_summary_dict(self) -> dict:
        with self._cost_lock:
            if (self.request_count and not self._in_flight_requests
                    and self._final_attempted_request_count != self.request_count):
                self._final_attempted_request_count = self.request_count
                if self._refresh_usage(final=True):
                    self._finalized_request_count = self.request_count
            return {
                'model': self.model, 'session_id': self.session_id,
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
                'key_usage_delta_usd': None if self._baseline_usage is None and self._accounting_failed else self.total_cost,
                'unknown_cost_requests': self.unknown_cost_requests,
                'cost_tracking_complete': (not self._accounting_failed and self._in_flight_requests == 0
                                           and (self.request_count == 0 or self._finalized_request_count == self.request_count)),
                'cost_basis': 'gateway_key_usage_delta',
            }

    def get_cost_summary(self) -> str:
        return '\nTRANSLATION COST SUMMARY\n' + json.dumps(self.get_cost_summary_dict(), indent=2)

    def check_cost_limit(self) -> bool:
        with self._cost_lock:
            return not self._accounting_failed and (self.max_cost <= 0 or self.total_cost < self.max_cost)

    def should_translate(self, text: str) -> bool:
        return bool(text and text.strip()) and self.check_cost_limit()
