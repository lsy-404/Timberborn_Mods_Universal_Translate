# Progress

- Loaded agent-mode v0.3, searched relevant memory narrowly and verified repositories/workflows live. Delegated client and run investigation in isolated worktrees; no workflow run or provider request has been triggered.

- Integrated isolated client commits through f17a6de, recovery/gateway/CI commit 76c0c23, and PR CI inclusion commit 86ad265; all new data changes visible in the local fast-forward are already upstream history, not task output.
- Merged local checks passed 84 assertions: production 16, recovery 23, name pool 15, formatting/context 16, gateway 6, key accounting 8. Tests mock status/usage calls. Existing POSIX artifact durability checks are left to Linux CI.
- Status API reports quota_per_unit=1000000000; public pricing returns 403. The client reads authenticated key counter deltas and keeps two concurrent requests; max_cost is a stop threshold rather than a pre-request hard reservation. A temporary server-side test budget will bound the actual smoke.
- Main has effective rules requiring PR; automations environment deployment policy permits only main. The reviewed branch/PR will be checked and ordinarily merged where allowed before verify_only uses its existing secret. No rule or environment policy is changed.

- PR 41 passed both Translation contracts and Translation artifact safety checks and merged normally under repository rules at cc41ee65aeb05f9553819f53b69ca84f5327032e. No protection or environment policy was bypassed.
- Temporary daily $0.50 budgets were applied to the two existing pearl-only service keys after exact scope/readback checks; protected prior policies stored root-only at /var/lib/llm/tmut-test-budgets/1793578231266580742. Source funding curves and model whitelists are unchanged. These temporary policies must be restored after verification.

- First verify_only run failed during standalone production contracts before the live verification step. Temporary key policies were immediately restored and verified against saved values; no paid translation requests occurred.
- Integrated bbc50cd test-only repair: independent production suite passes 17 assertions with a fresh tokenizer cache; other five groups pass 68. Added a no-network byte-dispatch regression and kept context bounds unchanged.
