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

- PR 42 passed checks and merged at 014f4410c3a68db1eabfdb883e72c8d6132ef53c. Its bounded verify run stopped at usage validation before any completion; temporary policies were restored again.
- Integrated a566f45 envelope correction and real-shape test fixtures; all six focused suites pass 86 assertions. No provider call is made by these checks. Preparing the corrected client for ordinary protected-branch delivery and a fresh bounded run.

- PR 43 merged at 8cc1b8296dcc743d42fb4df77be7c49e7f96b214 after checks. Run 41367172120 authenticated/read usage correctly, then its first completion was rejected before admission at zero cost; temporary test policies restored.
- Independent gateway probes used unique diagnostic admissions only; accepted controls were normally settled zero with providerUsage null, no provider calls and no remaining source reservation. Exact actual fixture standard text-parts control admitted successfully.
- Integrated d9a81cd producer-side removal of the private caching marker and actual POST structure regression. Full prompt content/order, glossary, formatting checks and normal text arrays are retained; 86 focused assertions pass.
