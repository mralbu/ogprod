# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Uses `uv` for everything Python (never pip) and `just` as the task runner:

- `just check` — lint (ruff check + format check), typecheck (ty), full test suite. Run before every commit.
- `just fmt` — auto-format and fix lint.
- `just test tests/test_store.py -k upsert -v` — any pytest args pass through; plain `just test` runs everything.
- `just ingest <source> [--origin mirror|publisher] [--year ...] [--month ...] [--revision ...] [--mirror ...] [--files ...] [--no-entities] [--refresh]` — sync from the mirror (default) or fetch + parse + upsert into the store (`$OGPROD_STORE`, else `~/.local/share/ogprod/store`).
- `just fetch <source> [...]` — download a source's raw files into `data/` without ingesting.
- `just notebooks` — re-execute the example notebooks in place (downloads data).

## Architecture

ogprod normalizes public oil & gas production data from regulators (Brazil ANP, Argentina, Norway Sodir) into two canonical tables, stores them locally as Parquet, and exposes it through one deep module, `ogprod.Store`. The chain is: **connector (or mirror) → `Store.ingest` → `Store` queries (CLI is a thin adapter)**. The top-level package exports only `Store`, `available`, `convert`, `Product`, `SchemaError`; connector internals live in `ogprod.sources` (`get`, `Source`, `register`, builders), mirror/manifest helpers in `ogprod.mirror`, and the schema in `ogprod.schema`.

- **`schema.py`** defines both canonical tables and is the contract everything else builds on: production (one row per `(entity, period, product, field)` — usually one per `(entity, period, product)`, but ANP splits a well's month across fields — volumes always in m³, `period` always month-start) and entities (one row per well/field, geometry always EPSG:4326 — Points for wells, (Multi)Polygon ring fences for fields). `validate`/`validate_entities` enforce the invariants (enums, non-null source/entity_id/period, unique `PRODUCTION_KEY` for production and `(source, entity_id)` for entities, lon/lat bounds); every parser validates its own output.
- **`sources/base.py`** is the connector interface. Each connector separates *parsing* (`parse`/`parse_entities` — pure, offline, takes a raw file) from *fetching* (`fetch`/`fetch_entities` — network, optional, injectable `httpx2.Client` so tests use `MockTransport`). Connectors hand columns to the shared assemblers `build_frame`/`build_entities`, which normalize ids (float-promoted numeric ids come out as clean integer strings), drop unkeyable rows (null id/period), and validate. Connector `fetch` options (`year`, `month`) are explicit keywords — no `**kwargs`, so typos raise `TypeError`. `restatement_order(paths)` orders overlapping files so the restating one is parsed last (ANP: pré-sal after mar). `@base.register` + an import in `sources/__init__.py` puts a connector behind `ogprod.available()` / `ogprod.sources.get()`.
- **`mirror.py`** implements the canonical Hugging Face Dataset mirror contract (`DEFAULT_MIRROR = "mralbu/ogprod"`, overridable via `mirror=` or `$OGPROD_MIRROR`), `manifest.json` schema v1 validation, safe path checking, streaming SHA-256/byte-length verified downloads over plain anonymous HTTPS (`httpx2`, no `huggingface_hub` runtime dependency), partition stats computation, publish sanity-gate checks, and non-destructive manifest merging.
- **`store.py`** holds `Store` (`ingest`, `production`, `entities`, `sql`, `coverage`) and its internal module functions. The store is strictly local-first plain files, not a database: production as hive-partitioned zstd Parquet (`<root>/production/source=*/year=*/data.parquet`, sorted by `entity_id` first with `row_group_size=128_000`), entities as one zstd GeoParquet per source. Writes **upsert on `(source, entity_id, period, product, field)` by atomically rewriting affected partitions — never append** (publishers restate history; `field` is in the key because ANP splits a well's month across several fields). DuckDB is the query engine only (`production()` with filter pushdown, `sql()` escape hatch over `production`/`entities` views), never the storage format. Root resolution: `Store(root)` → `$OGPROD_STORE` → `$XDG_DATA_HOME/ogprod/store` → `~/.local/share/ogprod/store` (a store, not a cache — contents may not be re-downloadable). Single-writer; interrupted ingests heal on re-run.
- **`Store.ingest`** defaults to `origin="mirror"` (checksum-skips unchanged local partitions/entities unless `refresh=True`, downloads and verifies via `httpx2`, atomically swaps, accepts `year=` int/iterable/None and `revision=`, rejects `month=` with `TypeError` pointing to `origin="publisher"`). `origin="publisher"` fetches live regulator files into a temp dir (or takes `files=`), parses in `restatement_order`, and writes in large batches; `store.combine_restatements` (keep-last *across* files) is what makes ANP's overlapping terra/mar/pré-sal files upsert instead of double-count. A key repeated *within* one parsed frame is a `SchemaError`, never silently deduped. **`__main__.py`** (`python -m ogprod ingest|coverage|fetch`) only maps flags onto `Store`.
- **`scripts/publish_mirror.py`** & **`scripts/ci_refresh.py`** (using the `publish` dependency group with `huggingface-hub`) publish local stores to HF atomically via `HfApi.create_commit(..., parent_commit=...)` after enforcing the sanity gate (>15% row/product-total drop, >10% entity drop, empty files; `--force` to override). `.github/workflows/update-data.yml` runs `ci_refresh.py` weekly with per-source fault isolation.

Key cross-cutting facts:

- `entity_id` is source-native and joins production⋈entities: ANP uses the well *name* (there is no separate stable code), Sodir the field NPDID, Argentina `idpozo`. `schema.canonical_id()` namespaces across sources.
- Units: everything stored in m³; `units.py` converts (`e3m3`/`e6m3` prefixes, never ambiguous "M").
- Publishers drift. Column-name drift is absorbed by alias tables at the top of each connector (`_META_ALIASES`, `_ENTITY_ALIASES`, …) — extend those, don't restructure. Download endpoints have all moved off their documented URLs at least once; each connector's module docstring records the live endpoint.
- Missing geometry is a null-geometry row, not an error (the entity still joins for metadata); rows without an id are dropped.

## Testing conventions

Fixtures in `tests/data/` mirror publishers' *real* formats (a few rows each); every connector has a rejection test for foreign files. Parsing must work offline — network code is only tested via injected `httpx2.MockTransport` (and mocked `HfApi` for publish tests). The one exception is `tests/test_live_smoke.py` (`@pytest.mark.live`, deselected by default, run weekly in CI): small real fetches that watch for publisher endpoint drift. When touching a connector's format handling, the repo's practice is to also verify against a live download and cross-check totals (see the "Format caveat" in docs/design.md).

## Docs

`docs/design.md` is the authoritative architecture doc (kept in sync with code — update it when behavior changes). `docs/data-sources.md` surveys candidate sources worldwide; `docs/downloading.md` covers per-source download details and manual fallbacks; `docs/follow-ups.md` tracks review findings and watch items; `docs/superpowers/` holds specs/plans/summaries from feature work.
