# ANP data quality + historical-data specs — build summary

**Date:** 2026-10-07 · **Range:** `0cd58c8..HEAD`
**Specs:** [data quality](../specs/2026-10-07-anp-data-quality-design.md) ·
[historical Wayback fetch](../specs/2026-10-07-anp-historical-wayback-design.md) ·
[Zenodo mirror](../specs/2026-10-07-anp-historical-zenodo-mirror-design.md) ·
**Open items:** [../../follow-ups.md](../../follow-ups.md)

## What was built

- **Repeated well-months are merged, not rejected.** After 0cd58c8 made
  in-file duplicate keys a `SchemaError`, 3 of the 357 current ANP CSVs
  failed to parse, which broke `just ingest anp`:

  | File | Repeated keys | Shape |
  |---|---|---|
  | `2005_producao_terra.csv` | 5,472 | both copies carry the oil; gas and production hours are each in only one copy |
  | `2019_05_producao_mar.csv` | 26 | one row per crude stream, identical volumes |
  | `2019_05_producao_presal.csv` | 3 | same as mar |

  `anp._merge_repeated_rows` collapses each `(well, period, field)` group:
  per product, the first non-zero value in file order. Non-zero values
  more than 0.5% apart still raise `SchemaError`. This replaces the old
  verbatim-row `drop_duplicates`.
- **Known publisher gaps.** `anp.KNOWN_GAPS` lists the 2003-07..2005-06 gas
  gap: only ~600 of ~8,300 wells report gas, ≈5% of national volume.
  `parse` emits a `UserWarning` when a file reports gas in that range.
  Nothing is imputed.
- **Specs for pre-2005 history** (not yet implemented). ANP no longer
  serves its 1941–2004 per-well CSVs. The Wayback Machine holds
  byte-identical 2018-08-27 captures of all 50 files (251 MB). The specs
  cover a checksum-pinned Wayback fetch, an old-format parse branch, and a
  CC-BY 4.0 Zenodo mirror as a fallback.

## Verification (live data, 2026-10-07)

- All 357 current CSVs parse.
- 2005 terra oil: 12.020 Mm³ summed, 11.841 Mm³ merged. The oil copies
  were the double count. The archived 2018 snapshot's totals equal the
  sum, so ANP's old export double-counted.
- National oil + condensate, keyed once as ingest does: 2005 1,634,
  2010 2,055, 2015 2,437, 2020 2,948, 2023 3,402 kbbl/d. All are in line
  with published national totals.
- Gas: 2002 15.5 bcm, then 7.8 / 2.4 / 9.0 bcm for 2003–2005, then
  17.7 bcm in 2006. This is the gap now flagged by `KNOWN_GAPS`.

## Decisions worth remembering

- **Merge, don't sum, don't drop.** Summing double-counts oil and dropping
  a copy loses gas. The archived files show the copies are one well-month
  split by an old state cross-join on field names that exist in two
  states (FAZENDA BOA ESPERANÇA BA/ES, FAZENDA BELÉM BA/CE).
- **Flag, never impute.** ogprod normalizes what publishers report.
  Backfilling gas would invent per-well volumes. A field-level backfill
  table is a possible later addition, not yet verified to exist for
  2003–2005.
- **Zenodo over Kaggle** for the mirror. Zenodo serves anonymous
  downloads, so `fetch` can use it, and records get a DOI. Kaggle needs an
  API token. Redistribution rests on Decreto 8.777/2016 and Resolução
  CGINDA 2/2017 (free use, attribution only).

## Next

1. Implement the historical Wayback fetch and old-format parse. It reuses
   `_merge_repeated_rows` with state-code preference and a rounding
   tolerance.
2. Zenodo sandbox upload, courtesy note to ANP, then publish and set
   `_HISTORICAL_MIRROR`.
