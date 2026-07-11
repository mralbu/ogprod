# Design

How `ogprod` is structured and why. For *which* data exists in the world, see
[data-sources.md](data-sources.md).

## Goals

1. One canonical table for production data from any public source, at well or
   field granularity, so cross-source analysis needs no per-source code.
2. Keep source-native identifiers and quirks recoverable — normalization must
   not destroy information you'd need to trace a number back to the publisher.
3. Parsing must work offline and be testable without network access; fetching
   is a separate, optional concern.

## Canonical schema

Every connector returns a long-format `pandas.DataFrame`, one row per
`(entity, period, product)`:

| Column | Meaning |
|---|---|
| `source` | connector name (`"anp"`, `"sodir"`, …) |
| `entity_type` | `"well"` or `"field"` |
| `entity_id` | source-native identifier: ANP well name (`Nome Poço`), Argentina `idpozo`, Sodir NPDID |
| `entity_name` | human-readable name |
| `field` | field the entity belongs to (equals `entity_name` for field-level sources) |
| `basin` | geological basin, when the source reports it |
| `region` | state / province / offshore area |
| `operator` | operating company, when reported |
| `period` | first day of the production month (`datetime64[ns]`) |
| `product` | `oil`, `gas`, `condensate`, `ngl`, or `water` |
| `volume` | volume produced over the period, **always in m³** |

Design choices behind it:

- **Long, not wide.** Sources report different product mixes (Sodir has NGL
  and condensate, Argentina doesn't). In long format a missing product is a
  missing row, not a schema change.
- **Volumes in m³ for every product.** One unit end to end beats per-product
  conventions; `ogprod.units.convert` gets you to `bbl`/`Mcf`/`e6m3` for
  display. Factors are the exact ISO definitions (1 bbl = 0.158987294928 m³,
  1 scf = 0.028316846592 m³). Unit names use an `e<power>` prefix (`e3m3` =
  thousand m³) because "M" is ambiguous — thousand in oilfield usage (`Mcf`,
  ANP's `Mm³`), million in SI.
- **Source-native IDs, namespaced on demand.** `entity_id` is whatever the
  publisher uses; `schema.canonical_id(source, entity_id)` (`"anp:7-TPI-1-RJS"`)
  gives a globally unique key. No invented global registry to maintain.
- **`period` is a month timestamp.** All current sources report calendar
  months. Daily sources (UK NSTA well data) would add a `frequency` marker or
  a resample step at ingestion; not needed yet.
- **Validation is a function, not a framework.** `schema.validate(frame)`
  checks columns, enum values, month-normalized periods, and numeric volumes,
  and every connector runs it on its own output (via `base.build_frame`).

## Entities

Alongside production, every connector can return a second canonical table —
entities — one row per well or field, as a `geopandas.GeoDataFrame` in
`EPSG:4326`:

| Column | Meaning |
|---|---|
| `source` | connector name, as in the production schema |
| `entity_type` | `"well"` or `"field"` |
| `entity_id` | source-native identifier — the join key with production |
| `entity_name` | human-readable name |
| `field` | field the entity belongs to |
| `basin` | geological basin, when reported |
| `region` | state / province / offshore area |
| `operator` | operating company, when reported |
| `status` | lifecycle when reported (producing / shut-in / abandoned); source-native free text |
| `geometry` | `Point` for wells; `Polygon`/`MultiPolygon` for field ring fences |

Everything joins to production on `(source, entity_id)`.

Design rules, enforced by `schema.validate_entities`:

- **Always EPSG:4326.** Connectors may parse a publisher's native CRS but
  must hand `build_entities` that CRS via `crs=...`; it reprojects.
- **Geometry type follows `entity_type`.** Wells are `Point`, fields are
  `Polygon`/`MultiPolygon` — any other geometry type is rejected.
- **Missing geometry is allowed.** A publisher that hasn't delineated a
  field yet, or a well with no recorded coordinates, is a row with
  `geometry = None`, not an error.
- **Ids are unique and non-null.** A duplicated `(source, entity_id)` would
  silently fan out (and double-count) every production join, so it is
  rejected; so are null ids. `build_entities` drops null-id rows up front —
  they can't join anything.
- **Coordinates are sanity-checked.** Geometry outside lon/lat bounds
  (|lon| ≤ 180, |lat| ≤ 90) is rejected — the usual cause is projected
  coordinates mislabeled as degrees.

`Source` splits entities the same way it splits production:

- `parse_entities(raw) -> GeoDataFrame` — pure, parses a raw geometry file.
  Connectors whose publisher has no geometry dataset leave it unimplemented
  (raises `NotImplementedError` pointing at `docs_url`).
- `fetch_entities(dest_dir, **kwargs) -> list[Path]` — network, downloads the
  publisher's geometry file(s). Also optional.

`base.build_entities(...)` is the entities counterpart to `build_frame`:
connectors hand it metadata series plus a geometry column, and it assembles
the GeoDataFrame, reprojects to EPSG:4326 if needed, and validates.

## Connectors

`sources/base.py` defines the `Source` ABC:

- `parse(raw) -> DataFrame` — **pure and mandatory.** Takes a path or file
  object of a raw file as published, returns a validated canonical frame.
- `fetch(dest_dir, *, client=None, **kwargs) -> list[Path]` — **optional.**
  Downloads the publisher's raw file(s) into `dest_dir` and returns their
  paths (a list, because ANP splits one month across terra/mar/pré-sal CSVs).
  All three built-in connectors implement it. `client` takes an injected
  `httpx.Client`, which is how the tests exercise fetch offline against an
  `httpx.MockTransport`; per-source parameters (`year`, `month`) arrive via
  keyword. Connectors without a stable download URL leave the base
  implementation, which raises `NotImplementedError` pointing at the
  publisher's download page.

`@base.register` adds a connector to the registry behind `ogprod.available()`,
`ogprod.get(name)`, `ogprod.load(name, raw)`, and `ogprod.fetch(name, dest,
**kwargs)`.

`base.build_frame(...)` is the shared assembler: connectors hand it metadata
series plus a `{Product: volumes_in_m3}` mapping and it melts, types, drops
missing volumes, and validates. Connectors stay ~80 lines of format knowledge.

### Per-connector notes

**`anp` (Brazil, well-level).** Semicolon-separated, comma decimals, dot
thousands separators. Bulk files use a stacked multi-row header (main labels
spanning sub-columns, e.g. `Gás Natural` over `Associado`/`Não Associado`/`Gás
Total`), which `parse` flattens into single names before matching. Header
names have drifted over the years, so headers are matched after stripping
accents and case. The volume unit is read from the header's parenthesis —
`(m³)`, `(Mm³)` (thousand m³), and daily rates like `(bbl/dia)`, which are
multiplied by the days in the month before conversion. Multiple columns for
one product are summed, except: a `Total` sub-column supersedes its components,
and `Petróleo` (= óleo + condensado) is ignored when an `Óleo` column is also
present, to avoid double counting. Accepts either `Ano`/`Mês` columns or a
single `Período` column (`2023/01` or `2023-01`). `fetch` scrapes the gov.br
open-data page for bulk zips (yearly 2005–2020, monthly 2021–2023, filename
conventions drift) and extracts the CSVs; omitting `month` downloads a whole
year, omitting `year` downloads the whole archive (multi-zip fetches extract
into per-period subdirectories). 2024+ exists only in the interactive CDP app,
so `fetch` raises a pointed error for those years. Note the pré-sal
file is a subset of the mar file — concatenating all three double-counts.
Well entities come from a separate cadastre: the documented "Tabela de
Poços" download page 404s, so `fetch_entities` queries ANP's GeoServer WFS
directly (`BD_ANP:POCOS_SIRGAS`, ~31k wells) as CSV with a WKT `GEOM` point
column; `parse_entities` also accepts the yearly "poços públicos" CSVs,
which instead carry explicit `LATITUDE_BASE_DD`/`LONGITUDE_BASE_DD`
columns. `entity_id` is the well name (`Nome Poço`) — ANP's cadastre has no
separate stable well code.

**`argentina` (well-level).** "Capítulo IV" CSVs: comma-separated, dot
decimals, stable lowercase column names (`idpozo`, `anio`, `mes`, `prod_pet`,
`prod_gas`, `prod_agua`). Oil/water in m³, gas in thousands of m³. `sigla` is
the human-readable well code; `idpozo` is the stable numeric ID. `fetch`
resolves the per-year CSV through the portal's CKAN API (`package_show`), so
no file URL is hardcoded; full-year files run 100–300 MB. Well entities come
from a further CKAN resource inside the *same* dataset — "Listado de pozos
cargados por empresas operadoras" — not a separate package (the documented
package id 404s). It has no `geojson` column like the production file;
coordinates are plain decimal degrees in `coordenadax`/`coordenaday` (lon/
lat). It reports the operator only as a short code, `idempresa` (e.g.
"YPF"), which is *not* directly comparable to production's `empresa`; the
closest analog to well status is `clasificacion` (e.g. "EXPLOTACION").

**`sodir` (Norway, field-level).** FactPages `field_production_monthly` CSV:
oil/NGL/condensate/water in millions of Sm³, gas in billions of Sm³. The
NPDID (`prfNpdidInformationCarrier`) is the stable entity ID; the oil
equivalent column (`prfPrdOeNetMillSm3`) is derived, not a product, and is
ignored. `fetch` hits the FactPages report-server CSV export directly — one
fixed URL returns the whole table (~2 MB). Field ring fences are *not* a
downloadable shapefile — the documented `fldArea.zip` 404s — so
`fetch_entities` queries FactMaps' ArcGIS DataService directly (layer 7100,
`MapServer/7100/query`) for GeoJSON polygons in EPSG:4326. `parse_entities`
keeps fields with null/empty geometry as null-geometry rows (not yet
delineated fields still join for metadata), repairs self-intersecting rings
with `make_valid` before dissolving, and dissolves each field's several
polygons into one; `entity_id` is the field NPDID. Features without an NPDID
are dropped — nothing can join them.

### Format caveat

Fixture files in `tests/data/` mirror the publishers' real formats, and each
connector's `fetch`+`parse` was verified end-to-end against live downloads in
July 2026 (with totals cross-checked against published country figures). If a
publisher drifts a header again, extend the alias tables at the top of the
connector (`_META_ALIASES`, `_VOLUME_COLUMNS`) — that's the designated
extension point.

Endpoint drift is watched automatically: `tests/test_live_smoke.py` runs one
small live fetch+parse per source, deselected from the normal suite
(`pytest -m live` to run by hand) and scheduled weekly in CI
(`.github/workflows/smoke.yml`).

## Adding a connector

1. Create `src/ogprod/sources/<name>.py` with a `Source` subclass; decorate it
   with `@base.register` and set `name`, `entity_type`, `docs_url`.
2. In `parse`, read the raw file, build the period as month-start timestamps,
   convert each product's volumes to m³ with `ogprod.units`, and return
   `base.build_frame(...)`.
3. Import the module in `sources/__init__.py` (imports drive registration).
4. Add a small fixture in `tests/data/` mirroring the real file (a few rows is
   enough) and a test asserting entity IDs, period, unit conversion, and
   metadata mapping. Include a rejection test for files that aren't from this
   source.
5. **Optional:** if the publisher also has a geometry dataset (well
   coordinates, field ring fences), implement `parse_entities`/
   `fetch_entities` the same way — pure parse into `base.build_entities(...)`,
   separate network fetch — and add a fixture/test pair mirroring step 4.
   Missing geometry for individual entities is fine (leave it `None` — the
   row still joins for metadata); rows without an entity id are dropped by
   `build_entities`. An entirely missing geometry dataset means leaving both
   methods unimplemented.

Good next candidates, roughly in order of effort (see data-sources.md):
BSEE OGOR-A (well, bulk delimited), Petrinex Alberta (well, CSV), NSTA PPRS
(field, CSV/ArcGIS), NLOG and the Danish Energy Agency (field, spreadsheets).

## Store

Publishers restate history: Petrinex ships amendments, Texas revises months
after the fact, ANP republishes. `ogprod.store` (`src/ogprod/store.py`) is a
persistent local store built on top of canonical frames, upserting on
`(source, entity_id, period, product)` rather than appending. It's plain
files, not a database server:

```
data/store/
  production/source=anp/year=2023/data.parquet   # hive-partitioned Parquet
  entities/anp.parquet                            # GeoParquet, one file per source
```

- **Production** is Parquet, hive-partitioned by `(source, year)`. DuckDB is
  the query engine (`load_production`, `sql`), never the storage container —
  every file is a plain Parquet file readable directly with pandas, and the
  same layout could be served statically. `write_production(frame)` groups
  incoming rows by `(source, year)`; for each affected partition it reads the
  existing file, drops stored rows whose `(entity_id, period, product)`
  appears in the incoming batch, concatenates, and rewrites the partition
  atomically (temp file + `os.replace`). Partitions outside the incoming
  batch are untouched.
- **Entities** are GeoParquet, one file per source, whole-file replaced by
  `write_entities` — entity tables are small (10³–10⁴ rows), so merge logic
  isn't worth having.
- `load_production(source=..., field=..., product=..., start=..., end=...)`
  and `load_entities(source=..., entity_type=...)` are the read side; both
  return pandas/geopandas frames with the canonical columns. `store.sql(...)`
  is a DuckDB escape hatch over `production`/`entities` views.
- **Root resolution.** Every function takes `root=...`; when omitted it falls
  back to the `OGPROD_STORE` environment variable, then `data/store/`.
- **Single writer, idempotent.** Each partition write is atomic, but a
  multi-partition write interrupted midway leaves some partitions updated
  and others not — re-running the same ingest heals the store (upserts are
  idempotent). Two concurrent writers to the same partition race
  read-modify-write; the store assumes one writer at a time.
- **CLI:** `python -m ogprod ingest <source> [--year ... --month ...]
  [--entities]` chains fetch → parse → write against the resolved store
  root. Parsed frames are concatenated in fetch order and written in large
  batches (each `(source, year)` partition rewritten once per batch, not
  once per raw file); duplicate keys within a batch keep the last
  occurrence, so ANP's terra/mar/pré-sal split upserts correctly instead of
  double-counting — the pré-sal file's rows (a subset of mar's) replace the
  corresponding mar rows for the same `(entity_id, period, product)` rather
  than being summed with them.

### Website data contract (future work)

A future static site (map + dashboards) will read from derived extracts, not
the store directly — pre-aggregated JSON rollups, a PMTiles layer for entity
geometry, and per-field sharded JSON for click-to-chart series. The contract
is specified but not yet built; see
[`docs/superpowers/specs/2026-07-10-storage-design.md`](superpowers/specs/2026-07-10-storage-design.md)
§4.
