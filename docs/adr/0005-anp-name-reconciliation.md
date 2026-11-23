# 0005. Reconcile ANP facility and field names

Status: accepted

## Context

ANP's production CSVs, the LPO platform list and the field WFS spell the same entities
differently and cover different sets of them.

## Decision

- **Facilities**: the LPO list is canonical. Pre-2022 production rows lose their trailing
  hull code (`PETROBRAS 66 (P-66)` → `PETROBRAS 66`), and known spelling drifts are
  mapped by hand in `brazil._FACILITY_ALIASES`.
- **LPO gas capacity** is published in `Mil m³/d`, which is already `e3m3/d`: it is stored
  without conversion.
- **Fields**: WFS polygons are the base. Missing metadata (region, environment, operator,
  start date) is backfilled from `extracao-campo*.csv`, and fields absent from the WFS
  (e.g. relinquished ones) are appended as metadata-only rows.

## Consequences

`_FACILITY_ALIASES` grows as new drifts are found; join-coverage warnings from
`ogprod audit` are how they surface.
