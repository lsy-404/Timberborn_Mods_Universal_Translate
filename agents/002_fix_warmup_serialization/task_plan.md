# Warmup completion for uncached responses

## Goal
Keep the first successful response as warmup, then allow normal configured request concurrency even when the gateway reports no cache usage fields.

## Scope
- Update `TranslatorLLM` warmup state and remove the unused rate-retry constant.
- Add a focused concurrent regression test using mocked HTTP responses without cache usage.
- Run focused translator and recovery tests; do not run the translation workflow or call the model.

## Plan
1. [x] Confirm current translator request/usage behavior and relevant test helpers.
2. [x] Rename warmup state to describe completed initial request; set it only after a valid translation succeeds.
3. [x] Add a concurrency test proving two responses without cache usage are not serialized after initial warmup.
4. [x] Run targeted tests, inspect diff, commit on `codex/tmut-warmup`, fast-forward local `main`, and push `main`.
