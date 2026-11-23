# Architecture decision records

Decisions that are not obvious from the code live here, not in code comments
(`just comments` bans new comments; see [0001](0001-no-code-comments.md)).
Add one with the next free number, using the sections of the existing records:
Context, Decision, Consequences.

| ADR | Decision |
| --- | --- |
| [0001](0001-no-code-comments.md) | No code comments: decisions go in ADRs, ratcheted by `just comments` |
| [0002](0002-brazil-not-refreshed-in-ci.md) | Brazil is not refreshed on GitHub Actions |
| [0003](0003-descriptive-user-agent.md) | Send a descriptive User-Agent to every portal |
| [0004](0004-duplicate-production-keys.md) | Duplicate production keys: reject by default, merge per source |
| [0005](0005-anp-name-reconciliation.md) | Reconcile ANP facility and field names against LPO and WFS |
| [0006](0006-historical-files-immutable.md) | Historical source files are immutable unless refreshed |
| [0007](0007-connector-registration-by-import.md) | Connectors register themselves when `ogprod.sources` is imported |
| [0008](0008-web-explorer-loading.md) | Web explorer: rollups first, wells lazily, field bounds, GeoJSON fallback |
