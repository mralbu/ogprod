# Follow-ups

Findings from the storage-feature reviews (July 2026) and their status.
All items from the 2026-07-11 review pass were **fixed on 2026-07-11**
(commits `1861aee`…`f322516` plus the docs commit that updated this file);
the fixes are summarized below, followed by the still-open watch items.

## Fixed

1. **Float-promotion-fragile ids** (argentina `idpozo`, sodir NPDID) — id
   normalization now lives in `base.id_string`, applied by
   `build_frame`/`build_entities` for every connector: integral float
   columns route through nullable Int64, string ids (well names, leading
   zeros) pass through untouched.
2. **One dirty row aborted a whole parse** — `build_frame` drops rows with
   a null period or entity id (publisher dirt that can't be keyed), while
   `schema.validate` still hard-rejects nulls so nothing dirty reaches the
   store.
3. **Sodir silently dropped null-NPDID fields** — now an explicit,
   documented, tested policy: features without an NPDID are dropped
   (nothing can join them); `build_entities` enforces the same rule for
   all connectors.
4. **Blank ids misdiagnosed as duplicates** — `validate`/`validate_entities`
   reject null `entity_id` with a message that names the real problem; the
   duplicate-id sample is now sorted before truncation.
5. **Sodir dropped null-geometry fields** — they now survive as
   null-geometry rows per the documented invariant (metadata stays
   joinable); empty post-dissolve geometries convert to `None`.
6. **`ingest --entities` last-file-wins** — multi-file entity fetches are
   concatenated per source before the whole-file-replace write.
7. **Failed ingests reported nothing useful** — parse errors name the
   failing raw file, and paths written by earlier flushes are printed
   before a mid-run failure surfaces.
8. **Partition rewritten once per raw file** — parsed frames are batched
   (~2M rows) and written together; keep-last dedup within a batch
   preserves the pré-sal-over-mar restatement semantics.
9. **No cp1252 fallback in ANP's cadastre reader** — `_read_entities` now
   falls back exactly like `_read`.
10. **Duplicated CKAN flow in argentina** — `fetch`/`fetch_entities` share
    `_csv_resources` + `_download_resource`.

Also fixed: coordinate-bounds validation (|lon| ≤ 180, |lat| ≤ 90) in
`validate_entities`; the always-skipping registry test now exercises the
base class directly; the empty-store `entities` SQL view types `geometry`
as BLOB to match a populated store; `_UPSERT_KEY`/`_SORT_KEY` merged.

## Fixed later (2026-07-11, notebook refresh)

- **Upsert key dropped multi-field wells (~55% of ANP offshore oil).** ANP
  reports one well's month split across several fields (e.g. `1-BRSA-1146-RJS`
  under ATAPU / ATAPU_ECO / OESTE DE ATAPU), so upserting on
  `(source, entity_id, period, product)` collapsed those rows keep-last. Found
  while rewriting notebook 02 onto the store (store total 1.46 M bbl/d vs
  3.36 from the raw files). The key now includes `field` (NA-safe); pré-sal
  restates mar rows identically down to the field, so keep-last dedup still
  removes exactly the restatements (commit `2ca1ba9`, regression-tested).
  Anyone with a store written before that commit should re-ingest ANP.

## Fixed 2026-10-07 (review of main)

- **Silent keep-last dedup inside one file.** `write_production` used to
  drop every repeated `(source, entity_id, period, product, field)` keep-last,
  for every source — fine for ANP's cross-file restatements, but it would
  also have swallowed genuinely distinct rows *within* a file (the class of
  bug behind the multi-field loss above). `schema.validate` now rejects a
  repeated `schema.PRODUCTION_KEY`; keep-last lives only in
  `store.combine_restatements`, which ingest applies across files. Checked
  against live data: Argentina 2026 (661k rows) and ANP 2010/2015/2020/
  2021-08/2023-12 have no conflicting repeats. ANP's
  `2015_producao_presal.csv` has two rows repeated verbatim (every column,
  contract and installation included); the ANP parser now drops verbatim
  repeats only. The 2015 archive ingests to 2.42 Mbbl/d mean oil.
- **Argentina fetch picked the first name containing the year.** The portal
  lists 2024 and 2025 twice (the newer copy adds an `id` column) plus
  `(DDJJ abiertas y cerradas)` variants, so the choice hung on CKAN's
  listing order. Now: exact-name match, DDJJ excluded, newest `created`.
- **Per-value unit conversion.** Connectors converted with
  `.map(lambda ...)`; now one vectorized multiply by `units.factor(unit)`.
- **`store.sql()` leaked its DuckDB connection** — now closed after the query.

## Open watch items

- **Upserts never remove or move rows.** A restatement that withdraws a
  well-month, or reassigns it to a renamed/corrected field (`field` is in
  the key), leaves the old row in the store — double-counting in the
  rename case. Safe for today's sources (pré-sal restates mar identically
  down to the field); revisit before Petrinex amendments land. Options:
  replace whole `(source, period)` slices per ingest (needs batches that
  never split a month's files) or an explicit `--replace` mode.
- **ANP "oil" changes meaning across vintages.** Files with only `Petróleo`
  count condensate as oil; files with `Óleo` split it out, so a
  condensate-producing well's oil series can step at a format change. A
  file with `Petróleo` + `Condensado` but no `Óleo` would count condensate
  twice; no such layout has been seen yet.

- **ANP duplicate well names.** The live cadastre (31,273 rows, checked
  July 2026) has zero duplicate `POCO` values, so `validate_entities`'
  uniqueness check passes today. If ANP ever publishes a duplicate,
  `ingest anp --entities` will fail loudly and the connector will need an
  explicit dedupe policy.
- **Store is single-writer.** Concurrent writers to the same partition
  race read-modify-write (documented in design.md); revisit only if a
  scheduler ever runs overlapping ingests.
- **`load_entities` reads eagerly / multi-value `source` doesn't prune
  partition dirs.** Both fine at current scale; revisit when the site
  exporter (spec §4) lands.

## Empirically refuted (checked against live data, no action needed)

- ANP well names duplicating in the cadastre: zero duplicates in the live
  31,273-row WFS export (see watch item above).
- Pré-sal/mar write order mattering for stored values: overlapping rows in
  three sampled months are byte-identical, so upsert order is not
  load-bearing (it only mattered for the old concat-and-sum approach).
