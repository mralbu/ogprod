# Storage design: production store, entity geometry, and the static-site contract

Status: approved 2026-07-10.

## Goal

Give ogprod a persistent local store holding (a) full-history production time
series from every connector and (b) entity geometry — well coordinates and
field ring fences — such that:

1. Python users query it interactively through simple loader functions.
2. A future static website can be built from derived extracts, with no query
   server.

## Decision

Parquet/GeoParquet files are the storage format; DuckDB is the query engine
only, never the container. "Files as the database" is exactly the property a
static website needs, and DuckDB delivers database-grade query speed over
Parquet anyway.

Alternatives rejected:

- **Single DuckDB database file** — real SQL upserts, but an opaque
  single-writer binary that still needs an export step for the site.
- **Plain files, no store module** — no upsert semantics, no geometry join
  story, every consumer reimplements filtering.

## 1. Entities schema (new canonical table)

A second canonical table alongside production, one row per entity, returned
as a GeoDataFrame in EPSG:4326:

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

Geometry is a first-class connector output. Publisher geometry lives in
separate datasets from the production CSVs (ANP well tables, Argentina's well
registry, Sodir FactMaps), so the `Source` ABC grows two methods mirroring the
existing pair:

- `parse_entities(raw) -> GeoDataFrame` — pure, offline-testable; mandatory
  for connectors that support geometry, otherwise the base implementation
  raises `NotImplementedError` pointing at the publisher's data page.
- `fetch_entities(dest_dir, *, client=None, **kwargs) -> list[Path]` —
  optional, same injected-`httpx.Client` pattern as `fetch`.

`schema.validate_entities(gdf)` checks columns, CRS (must be EPSG:4326),
enum values, and geometry-type-vs-entity-type (wells are points, fields are
polygons). Every `parse_entities` runs it on its own output.

`geopandas` becomes a core dependency.

## 2. Store layout and upsert semantics

```
data/store/
  production/source=anp/year=2023/data.parquet   # hive-partitioned Parquet
  entities/anp.parquet                            # GeoParquet, one file per source
```

Scale target: full history, all sources, well-level where available —
tens of millions of rows. Hive partitioning by `(source, year)` keeps
filtered reads to megabytes.

**Production upsert.** Publishers restate history (Petrinex amendments, ANP
republishes), so the store upserts on `(source, entity_id, period, product)`
and never appends blindly. `store.write_production(frame)` groups incoming
rows by `(source, year)`; for each affected partition it reads the existing
file, drops existing rows whose `(entity_id, period, product)` appears in the
incoming batch, concatenates, and rewrites the partition atomically (write to
a temp file, rename over the old one). Partition-scoped rewrite matches how
publishers actually restate (whole months/years) and never touches unrelated
partitions.

**Entities.** Whole-file replace per source. Entity tables are small
(thousands to tens of thousands of rows); merge logic isn't worth having.

## 3. Python API

```python
ogprod.store.write_production(frame, root=None)      # upsert per §2
ogprod.store.write_entities(gdf, root=None)          # replace per source
ogprod.store.load_production(source=..., field=..., product=...,
                             entity_type=..., start=..., end=...) -> pandas.DataFrame
ogprod.store.load_entities(source=..., entity_type=...) -> geopandas.GeoDataFrame
ogprod.store.sql("SELECT ...") -> pandas.DataFrame   # DuckDB escape hatch over the store
```

- `load_production` builds a DuckDB query over the Parquet glob with filter
  pushdown; all filter parameters are optional and combine with AND. `start`
  / `end` bound `period` inclusively.
- `root` defaults to `data/store/`, overridable per call and via an
  environment variable (`OGPROD_STORE`) for library users.
- `store.sql` registers the production and entities datasets as views
  (`production`, `entities`) and hands the query to DuckDB.
- CLI: `python -m ogprod ingest <source> [--year ... --month ...]` chains
  fetch → parse → write against the default store root.

## 4. Website data contract (defined now, built later)

A separate export step (`just site-data`) derives everything the static site
needs from the store. The site never reads the raw store. Target experiences:
aggregate dashboards, and a map with click-through to per-entity charts.

- **Dashboards:** pre-aggregated JSON — country/basin/field × product ×
  month rollups. Small, cacheable, fetched whole.
- **Map:** entity geometries as a single PMTiles file (wells clustered at low
  zoom; ring fences as a polygon layer). PMTiles serves via HTTP range
  requests, so it works on any static host.
- **Click-to-chart:** per-entity monthly series as JSON, sharded by field —
  one file per field containing all of its wells' series — to avoid millions
  of tiny files on hosts with file-count limits.

The same Parquet store also works under DuckDB-WASM over HTTP if ad-hoc
browser querying is ever wanted; nothing in this design blocks it.

Only the contract above is in scope. The exporter and the site itself are
future work.

## 5. Testing

- Round-trip: write a canonical frame to a tmp store root, load it back,
  assert equality.
- Restatement: write a month, write a revised version of the same month,
  assert no duplicate `(source, entity_id, period, product)` rows and that
  revised values win.
- Partition isolation: writing one `(source, year)` leaves other partitions'
  bytes untouched.
- `validate_entities` rejection tests (wrong CRS, well with polygon geometry,
  missing columns).
- Per-connector `parse_entities` tests against small real-format fixtures in
  `tests/data/`, mirroring the existing connector-test pattern, including a
  rejection test for files that aren't from that source.

## Out of scope

- The site exporter and the website itself (§4 defines only the contract).
- Daily-frequency production (design doc: add a `frequency` marker when a
  daily source lands).
- Cross-source entity identity resolution (canonical IDs remain
  source-namespaced).
