# 0004. Duplicate production keys

Status: accepted

## Context

A production table is keyed by `schema.PRODUCTION_KEY`. Some sources publish more than
one row for a key, for different reasons.

## Decision

- `schema.validate_production` rejects duplicate keys: a source must resolve them first.
- **Mexico** sums duplicate `(field_id, period)` field rows and `(entity_id, period)` well
  rows (`_aggregate_duplicate_keys`), e.g. a field reported separately onshore and offshore.
- **Brazil** merges ANP's known pre-2005 state cross-join duplicates **before** field
  split-merging, preferring the row whose state matches the well name's suffix. Two
  non-zero values conflict, raising `SchemaError`, only when they differ by more than
  `_MERGE_RTOL = 0.5%` **and** more than `_MERGE_ATOL = 0.5` (in each rate column's
  canonical unit, e.g. `0.5 bbl/d` ≈ 2.4 m³/month for liquids, covering the 1–2 m³
  rounding differences between the spurious cross-join row and the authoritative row on
  small wells such as FAZENDA BELÉM, 2003-12: Bahia 228.6 m³ vs Ceará 230 m³).
- **Brazil proportional AIP splits**: after duplicate checks complete, `_sum_merged_field_rows`
  canonicalizes Transfer-of-Rights Surplus (`*_ECO`), open-acreage (`AnC_*`), and
  retired unitized offshore slices (`SERRA`/`ARATUM` → `MACAU`) to their parent field
  and **sums** colliding `(entity_id, field_id, facility_id, period)` rate rows across
  all production and injection columns (preserving `NULL` when all inputs are `NULL`).
  ANP allocates each well's monthly production and injection proportionally across these
  contract slices, so summing conserves 100% of physical volume.

## Consequences

A new source with duplicates fails validation until its merge rule is decided and
recorded here. Non-overlapping renames (and ANP's pre-2005 duplicate `NORTE DE PESCADA`
→ `DENTAO`) run before duplicate detection so spurious duplicates are never accidentally
summed; basin homonyms (`FAZENDA BELEM`, `BREJINHO`, etc.) run after duplicate detection
so cross-join rows sharing the raw name are deduplicated first.
