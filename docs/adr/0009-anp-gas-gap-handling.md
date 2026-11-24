# 0009. ANP 2003-07..2005-06 gas reporting gap handling

Status: accepted

## Context

In ANP's well-level production CSVs (`arquivos-producao-de-petroleo-e-gas-natural-nacional/{pm,pt}`),
gas reporting drops to ~420–650 of ~8,300 producing wells (~5% of actual national output) for
the 24 months from `2003-07` through `2005-06`, while oil and water are unaffected.

ANP publishes a separate monthly aggregate open-data series by state and onshore/offshore
(`ppgn-el/producao-gas-natural-1000m3.csv`, 1997–present) that is continuous through 2003–2005
and agrees with `ogprod`'s well-level totals to within 0.04% on adjacent intact years
(2002: −0.014%, 2006: −0.039%). No equivalent public ANP dataset exists at basin, field,
facility, or well grain for 2003–2005.

## Decision

- **Layer 1 (`data/store/production/`) stays strictly faithful to ANP well-level CSVs:**
  `ogprod` never imputes well-level rows and emits a `UserWarning` on parse when a file
  overlaps `brazil.KNOWN_GAPS`.
- **Layer 2 `country_monthly` backfills Brazil `gas_prod` for `2003-07..2005-06`:**
  `export_web` splices the 24 immutable official ANP `ppgn-el` monthly national gas rates
  (`brazil.NATIONAL_GAS_GAP_VOLUMES_E3M3`) onto existing Brazil rows in `country_monthly`
  (never inventing periods absent from Layer 1). Storing the 24 historical constants in
  `brazil.py` keeps `export-web` offline and CI-friendly (see [ADR 0002](0002-brazil-not-refreshed-in-ci.md)
  and [ADR 0006](0006-historical-files-immutable.md)).
- **Layer 2 `basin_monthly` masks Brazil `gas_prod` to `NULL` during `2003-07..2005-06`:**
  Basin charts show a clean gap (`connectNulls: false`) instead of a misleading plunge to ~5%.
  Field and facility rollups keep their reported rows untouched so the ~600 wells that did
  report gas remain visible.
- **Web explorer scopes warnings and shades the gap:**
  `meta.json` marks the gap with `resolved_in_country_rollup: true`. `DashboardPane` hides
  country-resolved gaps on overview views, only shows warnings when the displayed series
  overlaps `[start, end]` with `gas_prod` active, prefixes them with the source name, and
  renders an ECharts `markArea` band clamped to the overlapping periods.

## Consequences

- National (`Brazil` and `All Sources`) overview charts show continuous, official ANP gas
  totals without a landing-page warning banner.
- `SUM(basin_monthly.gas_prod)` is intentionally lower than `country_monthly.gas_prod`
  for Brazil during `2003-07..2005-06`.
- Post-2005 Brazilian fields, FPSOs, and wells no longer display an unrelated 2003–2005 warning.
