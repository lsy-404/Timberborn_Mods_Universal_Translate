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

- PR44 passed offline contracts41368372211 and merged normally as49a01ef. Real bounded run41368466875 exercised actual pearl inference but stopped on usage-query429 after9POSTs/8valid translations. Downloaded its logs/sample artifact, restored temporary policies1793580043243595579, and delegated shared low-frequency accounting plus read-only rate-limit/settlement diagnosis.

- Shared snapshot fixc73be30 passed89 focused offline assertions. PR45 passed Linux offline-contracts41368932568 and was normally merged. The next inference verification is deferred until the known Retry-After window has naturally expired (01:01:33UTC), with no restart or rate-limit change. Source/key scopes and budgets remain unchanged.

- At01:01:42UTC, after the known20-minute Retry-After window, dispatched fresh verification41370278257 from main89d0d91. Only confirmed translation key2 received a temporary USD0.50 daily policy (backup1793581194916808521); key4 was observed but not changed. Native baseline at1793581206: key2used2,060,100nanos/45965attempts; key4used7000/0attempts.

- Completed real verification41370278257 successfully; downloaded translation-logs-336 under the authorized outputs directory. Manually reviewed all localized sample strings and checked the real verification.json for commit,13languages,37success/0failure, no unresolved/unknown, and complete final usage reading.
- Final ledger reconciliation confirmed37Native attempts and37controller admissions settled,USD0.0085204actual, no pending/unknown and unchanged key4. Restored the temporary key2policy with exact readback. Implementation and live core translation validation are complete; full backlog publication remains the existing scheduled workflow scope.
