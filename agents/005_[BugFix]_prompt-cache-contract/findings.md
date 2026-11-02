# Findings

- The previous successful real sample returned81204input tokens,0cache reads/writes,800output tokens. Fixing rejected private prompt_cache_breakpoint fields removed those markers; zero caching must now be traced end to end rather than treating translation success as cache validation.

- Current official OpenAI Chat schema supports prompt_cache_breakpoint {mode:explicit} and request prompt_cache_options; the prior description as private/obsolete was incorrect. The gateway rejected a supported new field. Anthropic requires cache_control, and pinned New API drops cache fields during content reconstruction/conversion. Implement the documented public Chat boundary and provider-specific conversion without fixed physical versions/prices or prompt padding.
- User additionally requires one Session ID per translation job. The shared TranslatorLLM uses a generated UUID carried in session-id through every worker and retry; it remains outside prompt text, so it does not invalidate reusable prefixes. Existing server sticky routing is principal+alias+session scoped with eligibility-checked fallback.
