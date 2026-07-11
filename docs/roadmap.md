# Roadmap — improvement opportunities

Where the project could go next, as of July 2026. Ordered within each
section by value-for-effort. Immediate review findings live in
[follow-ups.md](follow-ups.md) (all currently closed); this file is the
longer view.

## Engineering foundations (cheap, high leverage)

- **CI.** There is none — `just check` only runs when someone remembers to.
  A single GitHub Actions workflow (`uv sync` + `just check`, maybe a
  3.11/3.14 matrix) would catch regressions and lock in the offline-test
  guarantee (the suite needs no network, so CI is trivial).
- **License + packaging metadata.** `pyproject.toml` has no `license`,
  `classifiers`, or `project.urls`. Required before anyone else can use or
  cite this; a PyPI release (`ogprod` appears unclaimed) would make
  `pip install ogprod`/`uv add ogprod` work.
- **Scheduled live smoke test.** Every publisher has already drifted off
  its documented endpoint once. A weekly CI job that runs one small
  `fetch`+`parse` per source (sodir is 2 MB) would surface drift when it
  happens instead of when a user hits it.
- **Notebook freshness.** The three example notebooks predate the store;
  they fetch+parse manually. Rewriting them on `just ingest` + `store.load_*`
  would halve their code and demo the intended workflow.

## Data breadth (the core value)

Next connectors, in rough effort order (details in
[data-sources.md](data-sources.md)):

1. **BSEE OGOR-A** (US Gulf offshore, well-level, bulk delimited) — clean
   bulk files, big basin.
2. **Petrinex Alberta** (well-level CSV) — huge well count; its amendment
   stream is the acid test for the store's upsert semantics.
3. **NSTA PPRS** (UK, field-level CSV/ArcGIS) — completes the North Sea
   picture next to Sodir; geometry available via ArcGIS like FactMaps.
4. **NLOG (Netherlands) / Danish Energy Agency** (field-level
   spreadsheets) — small, finishes the European offshore set.
5. **US state commissions** (Texas RRC first) — highest value, highest
   effort; lease-based reporting needs an `entity_type` beyond well/field.

Cross-source niceties once ≥4 sources exist: a `frequency` marker for
daily sources (NSTA well data), and possibly basin/region name harmonies
(keep source-native, add optional mapping tables — never destroy).

## Store and scale

- **ANP 2024+ gap.** Bulk files stop at 2023; recent data sits in the CDP
  application. Reverse-engineering CDP's export (or scraping its API)
  closes the two-year hole in the repo's biggest well-level source.
- **Store metadata/manifest.** A small `manifest.json` per store (source →
  last ingested period, row counts, ingest timestamps) would answer "how
  fresh is my store?" without scanning Parquet, and gives `ingest` a way to
  fetch only newer periods (incremental refresh) instead of re-downloading.
- **Multi-value source pruning + entity pushdown** in the loaders — noted
  in follow-ups as fine at current scale; revisit when a second well-level
  source lands full-history.
- **Compression/row-group tuning** for partitions once Petrinex-scale data
  arrives (zstd, sorted row groups by entity_id for better pushdown).

## Website (spec §4 — the exporter is the unbuilt half of the design)

- **`ogprod export-site` step**: pre-aggregated rollup JSON
  (country/basin/field × product × month), a PMTiles layer from the
  entities GeoParquet, per-field sharded series JSON. The store was shaped
  so this is a pure read-side transform.
- **Static site itself**: map (MapLibre + PMTiles) with click-to-chart,
  dashboard pages from the rollups. DuckDB-WASM over the hosted Parquet is
  a free power-user add-on since the store is already plain Parquet.
- Even before a site: a `just report` that renders a couple of matplotlib
  summary charts per source would make regressions in ingested data
  visible.

## API and data-quality polish

- **`store.load_production(..., unit="bbl")`** — conversion at the loader
  edge; today users convert manually per column.
- **Country/source registry metadata** — expose `docs_url`, country,
  license, and update cadence per connector (`ogprod.sources_info()`), so
  a future site's "sources" page is generated, not hand-written.
- **Data-quality checks as a module** — cross-checks the connectors
  currently only do ad-hoc at verification time (monthly country totals vs
  published figures, negative volumes, wells producing after abandonment).
  A `store.audit()` returning a findings frame would formalize it.
- **Entity history** — statuses and operators change over time; the
  entities table keeps only the latest snapshot. If change tracking ever
  matters, snapshot per fetch date (cheap: entities files are small).
