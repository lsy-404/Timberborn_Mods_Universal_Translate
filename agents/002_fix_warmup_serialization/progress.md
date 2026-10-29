# Progress

- Added a mocked concurrency regression test that warms the translator with a successful response that has no cache usage fields, then verifies two subsequent requests reach the HTTP mock concurrently.
- Renamed cache-specific warmup state to `_initial_request_completed` and set it after a successful initial translation.
- Removed the unused `MAX_RATE_LIMIT_RETRIES` constant.
- The focused gateway suite passes all 7 tests. The broader legacy recovery suite was attempted with available Python 3.14 dependencies; it remains incompatible with the migrated Haiku model and pricing fixture assumptions.
