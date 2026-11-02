# Findings

- Current TMUT is Timberborn_Mods_Universal_Translate, with production translation in GitHub Actions. Older memory concerned a separate Timberborn_Tools scheduled updater; that historical deployment was not treated as the current target. Both repository paths and current workflow runs were verified.
- IRIS workspace inventory contains neither repository; file-based audit workflow applies. Local primary contains unrelated untracked gitsync-location.txt, which is preserved.
- Latest failed Auto Translate Mods run is 41314812783 (2026-11-01 14:10 UTC). October 30/31 also show gateway source-control 403; October 29 instead reports unsupported text input. Precise current access must be checked with the newly authorized pearl key scope.

- Key usage reads use the existing inference-key read-only endpoint. The separate user quota-query permission does not suppress the key counter. Final verification will independently reconcile native/controller settlement; a counter can temporarily include precharges and other requests sharing the key.
- Protected main plus main-only automations secrets prevents live verification from the task branch; CI delivery must use the current repository rules, not assumptions from an older deployment.

- The first bounded verification (41365793855) stopped before provider use: globally patched requests.get intercepted tiktoken cold-cache dependency downloads, yielding a MagicMock body instead of bytes. Combined PR discovery and warm local cache had hidden this isolation bug. Scoped gateway-only dispatch preserves real dependency reads without allowing fake gateway credentials to escape.

- The second bounded run (41366541010) reached live initialization but sent zero provider requests: the key-usage endpoint returns code:true/object:token_usage, whereas status returns success:true. Initial mocks incorrectly used the status envelope for both. The complete native handler confirms integer quota units without scaling. The client now strictly implements each endpoint separately and rejects malformed flags/objects/counters.
