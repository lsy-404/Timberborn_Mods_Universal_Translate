# Haiku cache billing and migration test updates

## Goal
Use Haiku 5.5's correct cache-aware pricing for normalized usage, and restore production/recovery test coverage under the Haiku gateway contract.

## Scope
- Price ordinary input, cache reads, 5-minute/1-hour cache writes, and output at the official Haiku rates, with the over-100K tier.
- Keep unknown usage fail-closed when required token totals are absent or invalid.
- Update affected translator fixtures, config/request expectations, and warmup field references without a legacy-model compatibility path.
- Run production, recovery, formatting, name-pool, and gateway tests offline; do not run live translation smoke or workflows.

## Plan
1. [x] Inspect current pricing data and affected production/recovery test contracts.
2. [x] Implement cache-aware Haiku pricing from available normalized token details.
3. [x] Update affected fixtures and expected costs to the Haiku request contract.
4. [x] Run the focused offline suites with Windows UTF-8 mode, inspect the diff, commit, fast-forward local `main`, and push.
