# TMUT Project Index

## Goal
Automatically translate missing Timberborn mod localization strings with bounded use of an OpenAI-compatible LLM endpoint.

## Stack
Python 3.11, TOML configuration/data, `requests`, GitHub Actions, and threaded per-file/per-language translation.

## Modules
- `.github/scripts/translate_mods.py`: selects missing translations and dispatches work.
- `.github/scripts/util/translator.py`: OpenAI-compatible request client, local rate limiter, and run cost summary.
- `.github/config/config.toml`: language, concurrency, API, and local cost settings.
- `.github/workflows/translate.yml`: daily/manual translation runner and checkpoint/merge workflow.
- `data/`: localization source and translations.
- `test/`: focused local tests without live API calls.
