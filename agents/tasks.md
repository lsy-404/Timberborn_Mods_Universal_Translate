# TMUT Task Tracker
> Guideline version: v0.3.0
## Tasks
| ID | Task | Description | Motivation | Status |
| :--: | :-- | :-- | :-- | :--: |
| 001 | [Feature] gateway-translation |Route automatic translation through the shared gateway with bounded model, request rate, and concurrency (6 focused tests and Python/TOML syntax checks passed) | Consolidate billing and constrain upstream pressure | completed |
| 002 | [Fix] uncached-warmup-concurrency |Complete the initial request warmup after the first valid response even when the provider reports no cache usage (uncached concurrency regression passed; final migrated-suite validation is recorded in task 003) | Preserve configured parallel request admission on Haiku | completed |
| 003 | [Fix] haiku-cache-accounting |Account for Haiku cache reads and writes by TTL, and restore active gateway test coverage (81 focused offline tests passed across production, recovery, formatting, name-pool, and gateway suites) | Ensure local cost caps reflect actual Haiku usage | completed |
| 004 | [BugFix] pearl-translation-run | Use the stable lowest-tier alias in the real translation workflow and repair failed runs | Keep provider versions server-managed and restore successful translations | completed |
