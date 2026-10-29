# TMUT Task Tracker
> Guideline version: v0.2.2
## Tasks
| ID | Task | Description | Motivation | Status |
| :--: | :-- | :-- | :-- | :--: |
| 001 | [Feature] gateway-translation | Route automatic translation through the shared gateway with bounded model, request rate, and concurrency | Consolidate billing and constrain upstream pressure | ✅ Completed: 6 focused tests and Python/TOML syntax checks passed |
| 002 | [Fix] uncached-warmup-concurrency | Complete the initial request warmup after the first valid response even when the provider reports no cache usage | Preserve configured parallel request admission on Haiku | ✅ Completed: 7 focused gateway tests passed; legacy recovery tests remain stale from the model migration |
