# Architecture decision records

Cross-cutting decisions that are not obvious from the code live here.
Add one with the next free number, using the sections of the existing records:
Context, Decision, Consequences.

| ADR | Decision |
| --- | --- |
| [0002](0002-brazil-not-refreshed-in-ci.md) | Brazil is not refreshed on GitHub Actions |
| [0003](0003-descriptive-user-agent.md) | Send a descriptive User-Agent to every portal |
| [0004](0004-duplicate-production-keys.md) | Duplicate production keys: reject by default, merge per source |
| [0005](0005-anp-name-reconciliation.md) | Reconcile ANP facility and field names against LPO and WFS |
| [0006](0006-historical-files-immutable.md) | Historical source files are immutable unless refreshed |
| [0007](0007-connector-registration-by-import.md) | Connectors register themselves when `ogprod.sources` is imported |
| [0008](0008-web-explorer-loading.md) | Web explorer: rollups first, wells lazily, field bounds, GeoJSON fallback |
| [0009](0009-anp-gas-gap-handling.md) | ANP 2003–2005 gas gap: Layer-1 faithful, Layer-2 country backfill & basin mask |
| [0010](0010-basin-and-region-enrichment.md) | Keep `basin` and `region` distinct; enrich Mexico via `geo_petro_prov`, fall back in Layer 2 |
| [0011](0011-multi-country-scaling-and-overview.md) | Multi-country scaling: tiered well granularity and split-line All Sources overview |
| [0012](0012-cookieless-analytics-goatcounter.md) | Cookieless GoatCounter analytics, opt-in at build time, virtual paths per selection |
