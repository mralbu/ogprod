# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Uses `uv` for everything Python (never pip) and `just` as the task runner:

- `just check` — lint (ruff check + format check), typecheck (ty), full test suite. Run before every commit.
- `just fmt` — auto-format and fix lint.
- `just test tests/test_store.py -k upsert -v` — any pytest args pass through; plain `just test` runs everything.
- `just fetch <source> [--year ... --month ...]` — download a source's raw files into `data/`.
- `just ingest <source> [--entities]` — fetch + parse + upsert into the Parquet store at `data/store/`.
- `just notebooks` — re-execute the example notebooks in place (downloads data).

## Architecture

ogprod normalizes public oil & gas production data from regulators (Brazil ANP, Argentina, Norway Sodir) into two canonical tables, stores them locally as Parquet, and exposes loaders. The chain is: **connector → canonical frame → store → loaders/CLI**.

- **`schema.py`** defines both canonical tables and is the contract everything else builds on: production (one row per `(entity, period, product)`, volumes always in m³, `period` always month-start) and entities (one row per well/field, geometry always EPSG:4326 — Points for wells, (Multi)Polygon ring fences for fields). `validate`/`validate_entities` enforce the invariants (enums, non-null source/entity_id/period, unique `(source, entity_id)` for entities, lon/lat bounds); every parser validates its own output.
- **`sources/base.py`** is the connector interface. Each connector separates *parsing* (`parse`/`parse_entities` — pure, offline, takes a raw file) from *fetching* (`fetch`/`fetch_entities` — network, optional, injectable `httpx.Client` so tests use `MockTransport`). Connectors hand columns to the shared assemblers `build_frame`/`build_entities`, which normalize ids (float-promoted numeric ids come out as clean integer strings), drop unkeyable rows (null id/period), and validate. `@base.register` + an import in `sources/__init__.py` puts a connector behind `ogprod.available()/get()/load()/fetch()`.
- **`store.py`** is plain files, not a database: production as hive-partitioned Parquet (`data/store/production/source=*/year=*/data.parquet`), entities as one GeoParquet per source. Writes **upsert on `(source, entity_id, period, product)` by atomically rewriting affected partitions — never append** (publishers restate history). DuckDB is the query engine only (`load_production` with filter pushdown, `sql()` escape hatch over `production`/`entities` views), never the storage format. Root resolution: explicit `root=` → `$OGPROD_STORE` → `data/store/`. Single-writer; interrupted ingests heal on re-run.
- **`__main__.py`** (`python -m ogprod`) has `fetch` and `ingest`. Ingest parses files in fetch order and writes in large batches; keep-last dedup within a batch is what makes ANP's overlapping terra/mar/pré-sal files upsert instead of double-count.

Key cross-cutting facts:

- `entity_id` is source-native and joins production⋈entities: ANP uses the well *name* (there is no separate stable code), Sodir the field NPDID, Argentina `idpozo`. `schema.canonical_id()` namespaces across sources.
- Units: everything stored in m³; `units.py` converts (`e3m3`/`e6m3` prefixes, never ambiguous "M").
- Publishers drift. Column-name drift is absorbed by alias tables at the top of each connector (`_META_ALIASES`, `_ENTITY_ALIASES`, …) — extend those, don't restructure. Download endpoints have all moved off their documented URLs at least once; each connector's module docstring records the live endpoint.
- Missing geometry is a null-geometry row, not an error (the entity still joins for metadata); rows without an id are dropped.

## Testing conventions

Fixtures in `tests/data/` mirror publishers' *real* formats (a few rows each); every connector has a rejection test for foreign files. Parsing must work offline — network code is only tested via injected `httpx.MockTransport`. When touching a connector's format handling, the repo's practice is to also verify against a live download and cross-check totals (see the "Format caveat" in docs/design.md).

## Docs

`docs/design.md` is the authoritative architecture doc (kept in sync with code — update it when behavior changes). `docs/data-sources.md` surveys candidate sources worldwide; `docs/downloading.md` covers per-source download details and manual fallbacks; `docs/follow-ups.md` tracks review findings and watch items; `docs/superpowers/` holds specs/plans/summaries from feature work.
