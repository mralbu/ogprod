# ogprod

[![CI](https://github.com/mralbu/ogprod/actions/workflows/ci.yml/badge.svg)](https://github.com/mralbu/ogprod/actions/workflows/ci.yml)

Public oil & gas production data pipeline, geospatial store, and web visualization data engine.

`ogprod` downloads public monthly oil, gas, condensate, NGL, water production and injection data alongside well, field, and platform/FPSO geometries, normalizes them into compact `float32` ZSTD Parquet and GeoParquet 1.1 tables, and queries them via DuckDB.

## Canonical Units

All production and injection columns are stored as **calendar-day average rates** in `float32`:

- **Liquids** (`oil_prod`, `condensate_prod`, `ngl_prod`, `water_prod`, `water_inj`): **`bbl/d`**
- **Gases** (`gas_prod`, `gas_inj`, `co2_inj`): **`e3m3/d`** (`10³ m³/d`, thousand m³/day)

Geospatial tables are stored as Hilbert-sorted **GeoParquet 1.1** (`EPSG:4326`, with `bbox` covering column):

- `store/geo/fields/<source>.parquet` (`Polygon` / `MultiPolygon`, e.g. `brazil.parquet`)
- `store/geo/facilities/<source>.parquet` (`Point` — platforms / FPSOs)
- `store/geo/wells/<source>.parquet` (`Point`)

## Built-in Sources

| Source | Country | Publisher | Production Grain | GeoParquet 1.1 Layers |
|---|---|---|---|---|
| `brazil` | Brazil (`BR`) | ANP (1941–present) | Well (oil, cond, gas, water + water/gas/CO₂ inj) | Fields, FPSOs/platforms (with capacities), wells |
| `norway` | Norway (`NO`) | Sodir (1971–present) | Field (oil, cond, NGL, gas, water) | Fields, facilities/platforms, wellbores |
| `mexico` | Mexico (`MX`) | SENER / CNIH SIH (1960–present) | Field (default) + well CSV/ZIP parser | Fields, installations/platforms, wells |

## Quickstart

By default, `ogprod ingest` downloads pre-normalized v2 Parquet and GeoParquet 1.1 files anonymously from the public Hugging Face mirror ([`mralbu/ogprod`](https://huggingface.co/datasets/mralbu/ogprod)) with no token required. Pass `--origin publisher` to fetch live from regulator portals.

```sh
uv run ogprod sources
uv run ogprod ingest brazil --year 2025 --year 2026 --data-dir ./data
uv run ogprod ingest norway --data-dir ./data
uv run ogprod ingest mexico --data-dir ./data
uv run ogprod coverage --data-dir ./data
uv run ogprod sql "SELECT source, field_name, round(sum(oil_prod), 0) AS oil_bpd FROM production WHERE period = '2025-01-01' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10" --data-dir ./data
```

Python API:

```python
import ogprod

db = ogprod.Store("./data")
db.ingest("brazil", year=[2025, 2026])  # default: origin="mirror"
db.ingest("norway", origin="publisher")  # live fetch from Sodir
db.ingest("mexico")

df = db.production(source="brazil", start="2026-01")
fields = db.fields()  # GeoParquet 1.1 polygons
facilities = db.facilities()  # GeoParquet 1.1 FPSOs/platforms
wells = db.wells()  # GeoParquet 1.1 well points
```

### Web Visualization (`site/`)

Export Layer-2 static assets (rollups, search index, `meta.json`, and `ogprod.pmtiles` via Felt's `tippecanoe`) and run the MapLibre + ECharts + lazy DuckDB-WASM explorer:

```sh
uv run ogprod export-web --sync-mirror --out-dir site/public/data --data-dir ./data
npm --prefix site ci && npm --prefix site run dev
```

See [docs/data-architecture.md](docs/data-architecture.md) for the full storage layout, source plan (Brazil ANP, Norway Sodir, Mexico SENER/CNH), and static web export contract.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

```sh
just check       # ruff format/lint + ty + pytest
just dead-code   # vulture
just complexity  # radon
```

## License

[MIT](LICENSE)
