# ogprod

[![CI](https://github.com/mralbu/ogprod/actions/workflows/ci.yml/badge.svg)](https://github.com/mralbu/ogprod/actions/workflows/ci.yml)

Public oil & gas production data pipeline, geospatial store, and web visualization data engine.

`ogprod` downloads public monthly oil, gas, condensate, NGL, water production and injection data alongside well, field, and platform/FPSO geometries, normalizes them into compact `float32` ZSTD Parquet and GeoParquet 1.1 tables, and queries them via DuckDB.

## Canonical Units

All production and injection columns are stored as **calendar-day average rates** in `float32`:

- **Liquids** (`oil_prod`, `condensate_prod`, `ngl_prod`, `water_prod`, `water_inj`): **`bbl/d`**
- **Gases** (`gas_prod`, `gas_inj`, `co2_inj`): **`e3m3/d`** (`10³ m³/d`, thousand m³/day)

Geospatial tables are stored as Hilbert-sorted **GeoParquet 1.1** (`EPSG:4326`, with `bbox` covering column):

- `store/geo/fields/source=<src>.parquet` (`Polygon` / `MultiPolygon`)
- `store/geo/facilities/source=<src>.parquet` (`Point` — platforms / FPSOs)
- `store/geo/wells/source=<src>.parquet` (`Point`)

## Quickstart

```sh
uv run ogprod sources
uv run ogprod ingest anp --year 2026 --data-dir ./data
uv run ogprod coverage --data-dir ./data
uv run ogprod sql "SELECT field_name, round(sum(oil_prod), 0) AS oil_bpd FROM production WHERE period = '2026-08-01' GROUP BY 1 ORDER BY 2 DESC LIMIT 10" --data-dir ./data
```

Python API:

```python
import ogprod

db = ogprod.Store("./data")
db.ingest("anp", year=2026)

df = db.production(source="anp", start="2026-01")
fields = db.fields(source="anp")  # GeoParquet 1.1 polygons
facilities = db.facilities(source="anp")  # GeoParquet 1.1 FPSOs/platforms
wells = db.wells(source="anp")  # GeoParquet 1.1 well points
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
