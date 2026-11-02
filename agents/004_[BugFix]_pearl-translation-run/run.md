# Run investigation

- 2026-11-01 run 41314812783: Run translation failed after HTTP 403 responses saying source control rejected request; partial data and logs were uploaded, merge skipped. The response does not identify the upstream cause.
- 2026-10-30 and 2026-10-31 runs reported the same 403. The 2026-10-29 run reported HTTP 400, Only text input is enabled; it has a distinct observed failure.
- Existing manual verify_only runs the production CLI on isolated data with 13 languages, 37 expected requests, $0.50 local threshold and 120 seconds, without translation publishing. The server-side key limit is needed to bound concurrent spending. Old failed runs cannot be repaired retroactively.
- Initial local historical gateway tests attempted a status read with a synthetic credential; no valid credential or completion request was used. Tests must mock gateway GET requests.
- Adapted recovery and gateway suites to mock status and usage GETs, assert server counter accounting and stop on uncertain charges; retained format recovery, budget and concurrent behavior checks.
- Focused local tests: recovery 23, gateway 6, accounting 8 passed with Python 3.11 virtual environment. Added gateway and accounting suites to the production workflow contract step. Commit 76c0c23.
