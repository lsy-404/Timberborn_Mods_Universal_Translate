# Findings

- Current translation makes one chat-completion request per missing entry/language. Configured 2-file and 2-language concurrency bounds the service to four in-flight calls.
- The request body now carries `max_tokens=1024`; translation prompts otherwise had no output bound.
- Haiku 5.5 official API pricing is $0.10/$0.50 per million input/output tokens through 100K prompt tokens and $0.50/$2.50 above 100K. Source: https://www.anthropic.com/claude/haiku
- The workflow now shares one non-canceling concurrency group across scheduled and manual runs. The client retries only HTTP 429 responses that provide a valid `Retry-After`, at most twice, clamped to 30 seconds; every attempt re-enters local RPM admission.
- [Initial test invocation] -> streamed a test snippet to Python without creating a file, then ran unittest discovery before the repository `test/` directory existed -> Python resolved its standard-library test package and ran 686 unrelated tests. No repository source was changed by that invocation; a dedicated test file was created and invoked directly afterward.
- The isolated worktree's global ignore file excludes `/agents` and `/test`; those required audit/test files must be explicitly force-added to the branch. The primary checkout's ignored `agent/` and untracked `gitsync-location.txt` remain untouched.
- [Command quoting error] -> an inline PowerShell/Python replacement expression was split at nested quotes -> no source change; reran the replacement through a bounded Python heredoc and the focused tests passed.
