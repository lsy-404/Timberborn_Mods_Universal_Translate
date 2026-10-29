# Findings

- The old `_cache_warmed` flag was set only when token usage reported cached or cache-write tokens. Haiku responses without those fields therefore left the warmup lock active for every subsequent request.
- The first valid translation response now completes initialization independent of cache counters. Failed or unmeasured responses do not complete warmup.
- `MAX_RATE_LIMIT_RETRIES` was unused; retry count remains bounded by `MAX_ATTEMPTS`.
- The existing `test_translation_recovery.py` still fabricates `gpt-6-luna` responses and asserts the previous cached-token price accounting. Running it against the Haiku-only client produced 17 failures and 3 errors from those stale assumptions, plus a Windows encoding error in the temporary partial-entry fixture. No test invokes a live model.
