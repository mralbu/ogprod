# 0004. Duplicate production keys

Status: accepted

## Context

A production table is keyed by `schema.PRODUCTION_KEY`. Some sources publish more than
one row for a key, for different reasons.

## Decision

- `schema.validate_production` rejects duplicate keys: a source must resolve them first.
- **Mexico** sums duplicate `(field_id, period)` rows (`_aggregate_duplicate_keys`), e.g.
  a field reported separately onshore and offshore.
- **Brazil** merges ANP's known pre-2005 state cross-join duplicates, preferring the row
  whose state matches the well name's suffix. Two non-zero values conflict, raising
  `SchemaError`, only when they differ by more than `_MERGE_RTOL = 0.5%` **and** more
  than `_MERGE_ATOL = 0.5 bbl/d`. The absolute tolerance (about 2.4 m³/month) covers the
  1–2 m³ rounding differences between the spurious cross-join row and the authoritative
  row on small wells (e.g. FAZENDA BELÉM, 2003-12: Bahia 228.6 m³ vs Ceará 230 m³).

## Consequences

A new source with duplicates fails validation until its merge rule is decided and
recorded here.
