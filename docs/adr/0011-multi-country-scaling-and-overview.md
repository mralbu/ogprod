# 0011. Multi-country scaling policy: tiered well granularity and split-line All Sources view

Status: accepted

## Context

Adding more jurisdictions to `ogprod` affects Layer 1 (the Python package and Hugging Face
mirror) and Layer 2 (the static Web Explorer hosted on GitHub Pages) very differently:

- **Layer 1 scales selectively per source:** users ingest only the jurisdictions they need
  (`ogprod ingest <source>`), and production is Hive-partitioned by `(source, year)` alongside
  per-source GeoParquet files.
- **Layer 2 is a single static bundle:** `export-web` currently builds one global
  `search/index.json` containing every field, facility, and well across all sources, eagerly
  loads cross-source summary JSONs on page boot, builds one global `ogprod.pmtiles` within
  GitHub Pages' 1 GB artifact limit and a 30-minute workflow timeout, and sums all countries
  together on the `"All Sources"` overview chart.
- **Not all jurisdictions have the same footprint:** offshore and compact regimes typically
  have hundreds to low tens of thousands of high-rate wells where individual well histories are
  analytically valuable. Mature onshore and shale basins (e.g. Mexico wells, Argentina, US
  onshore states, Western Canada/Petrinex) have 50k–1M+ low-rate wells that would dominate
  mirror size, `search/index.json` memory/typing latency, PMTiles size, and CI build time.
- **Summing heterogeneous countries creates chart artifacts:** because regulators start in
  different decades (1941, 1960, 1971, 1975, 2003, 2014...) and publish with different lags
  (1–6 months), summing all sources into a single `"All Sources"` line causes artificial
  left-edge step-ups and a right-edge lag cliff that also drops the `Latest Oil` / `Latest Gas`
  KPI cards whenever one source publishes ahead of the rest.

## Decision

1. **Tiered well granularity by well count and offshore character:**
   - **Offshore / low-well-count sources (< ~30k producing wells)** — e.g. `brazil`, `us-gom`,
     `us-ak`, `canada-nl`, plus compact offshore field-level regimes (`norway`, `uk`,
     `netherlands`, `denmark`) — keep well-level production where published by the regulator
     and retain well layers in the mirror and Web Explorer.
   - **High-well-count onshore sources (> ~30k wells)** — e.g. `mexico`, `argentina`, US
     onshore states, Western Canada — default to **field-level** production in the public
     Hugging Face mirror and Web Explorer (ingesting a publisher field rollup where available,
     or aggregating well rows to fields during ingestion) and omit high-volume `geo/wells`
     from Layer-2 web artifacts (`search/index.json` and `ogprod.pmtiles`).
   - **Local Python opt-in remains allowed:** connectors for high-well-count sources may still
     support optional local well-level parsing (`origin="publisher"` / `--files` or an explicit
     opt-in flag, following `mexico.py`) without pushing millions of well rows to the public
     mirror or static site.

2. **Index and query only producing well series in the Web Explorer:**
   - `export_web` includes a well in `search/index.json` only when it has well-level
     production history (avoiding dead-end search hits for exploration/dry wellbores and
     field-level-only sources).
   - The frontend skips DuckDB-WASM Parquet queries when a clicked wellbore belongs to a
     field-level source (`entity_type !== "well"`) or has no recorded production years,
     displaying static wellbore metadata instead.

3. **Split-line `"All Sources"` overview instead of a summed total:**
   - On the `"All Sources"` overview (`activeSource === null`, `selection.kind === "overview"`),
     render **separate per-source comparison lines** for the active metric (each source
     starting at its own `first_period` and ending at its own `last_period`), rather than
     summing all countries into one total line. Once the catalog exceeds ~6–8 sources, display
     the top sources with an optional grouped `"Other"` series or source filter.
   - Never forward-fill or impute lagging months (consistent with
     [ADR 0009](0009-anp-gas-gap-handling.md)).
   - Single-source views (`brazil`, `norway`, `mexico`, `uk`, ...) and entity views (`basin`,
     `field`, `facility`, `well`) retain the existing multi-product chart.
   - Overview KPI cards must not read from a ragged summed final month.

4. **Deferred scaling triggers (apply when crossed, not prematurely):**
   - **~6–10 sources:** replace the flat `TopBar` source pill strip with a scalable source
     switcher (supporting sub-national slugs like `us-gom`, `us-ak`, `canada-nl`) and compute
     default map overview bounds dynamically from `meta.sources` bounding boxes instead of a
     hardcoded Atlantic center.
   - **Larger mirrors / >8 CI sources:** parallelize `update-data.yml` per source, cache
     Hugging Face sync in `deploy-site.yml`, shard well search by source if needed, and move
     per-source licence/gap metadata onto the `Source` base class.

## Consequences

- The public Hugging Face mirror, `search/index.json`, `ogprod.pmtiles`, and GitHub Pages
  builds stay compact and fast as new countries are added.
- High-well-count onshore basins remain accessible in the Web Explorer at field/basin/country
  grain while still allowing optional local well-level analysis in Python.
- The `"All Sources"` landing view eliminates both right-edge lag cliffs and left-edge
  archive-start step-ups, and doubles as an immediate cross-country comparison chart.
- Cross-country combined-total charts are intentionally not the default on `"All Sources"`;
  users needing custom multi-country sums can query `country_monthly.parquet` or Layer 1
  directly with DuckDB.
