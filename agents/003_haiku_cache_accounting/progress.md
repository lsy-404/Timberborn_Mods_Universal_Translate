# Progress

- Added separate ordinary, cache-read, 5-minute write, 1-hour write, and output pricing for Haiku 5.5, including the over-100K tier.
- Parsed LiteLLM's normalized TTL breakdown, charged an unclassified cache-write remainder at the higher 1-hour rate, and reserved each request at the 1-hour write ceiling.
- Updated active model fixtures, request assertions, warmup test state, and missing-usage cases to Haiku's current contract; updated the production verification cost formula.
- Focused offline suites pass: production (19), recovery (24), format/context (16), name-pool (15), gateway (7), total 81 tests. The live smoke script and workflow were not run.
