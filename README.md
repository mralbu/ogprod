# ogprod

[![CI](https://github.com/mralbu/ogprod/actions/workflows/ci.yml/badge.svg)](https://github.com/mralbu/ogprod/actions/workflows/ci.yml)
[![Data Mirror](https://img.shields.io/badge/Data%20Mirror-Hugging%20Face-FFD21E?logo=huggingface&logoColor=000)](https://huggingface.co/datasets/mralbu/ogprod)

Public oil & gas production data pipeline, geospatial store, and web visualization data engine.

[![Launch Web Explorer](https://img.shields.io/badge/Launch_Web_Explorer_%E2%86%92-0969da?style=for-the-badge)](https://mralbu.github.io/ogprod/)

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
| `uk` | United Kingdom (`GB`) | NSTA (1975–present) | Field (oil, cond, gas, water + water/gas inj) | Fields (offshore + onshore), surface facilities, wellbores |
| `argentina` | Argentina (`AR`) | Secretaría de Energía (2006–present) | Field rollup (default; oil, gas, water + water/gas/CO₂ inj) + local well parser | Fields (Yacimientos), wells (Capítulo IV) |
| `netherlands` | Netherlands (`NL`) | NLOG / TNO (2003–present) | Well (oil, cond, gas, water + water/gas inj) | Fields, facilities/platforms, wells |

## Quickstart

By default, `ogprod ingest` downloads pre-normalized v2 Parquet and GeoParquet 1.1 files anonymously from the public Hugging Face mirror ([`mralbu/ogprod`](https://huggingface.co/datasets/mralbu/ogprod)) with no token required. Pass `--origin publisher` to fetch live from regulator portals.

```sh
uv run ogprod sources
uv run ogprod ingest brazil --year 2025 --year 2026 --data-dir ./data
uv run ogprod ingest norway --data-dir ./data
uv run ogprod ingest mexico --data-dir ./data
uv run ogprod ingest uk --data-dir ./data
uv run ogprod ingest argentina --year 2025 --year 2026 --data-dir ./data
uv run ogprod ingest netherlands --data-dir ./data
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
db.ingest("uk")
db.ingest("argentina", year=[2025, 2026])
db.ingest("netherlands")

df = db.production(source="brazil", start="2026-01")
fields = db.fields()  # GeoParquet 1.1 polygons
facilities = db.facilities()  # GeoParquet 1.1 FPSOs/platforms
wells = db.wells()  # GeoParquet 1.1 well points
```

### Web Visualization (`site/`)

Export Layer-2 static assets (rollups, search index, `meta.json`, `llms.txt`, and `ogprod.pmtiles` via Felt's `tippecanoe`) and run the MapLibre + ECharts + lazy DuckDB-WASM explorer:

```sh
uv run ogprod export-web --sync-mirror --out-dir site/public/data --data-dir ./data
npm --prefix site ci && npm --prefix site run dev
```

See [docs/data-architecture.md](docs/data-architecture.md) for the full storage layout, built-in source plan (Brazil ANP, Norway Sodir, Mexico SENER/CNH, UK NSTA, Argentina Secretaría de Energía, Netherlands NLOG), and static web export contract, and [docs/data-sources.md](docs/data-sources.md) for the researched global survey of candidate countries and regulators.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md).

```sh
just check       # ruff format/lint + ty + pytest
just dead-code   # vulture
just complexity  # radon
```

## License

- **Code & documentation:** [MIT](LICENSE) — applies to the `ogprod` Python package, scripts, and web explorer source code.
- **Data:** `ogprod` does not relicense third-party public datasets. Production and geospatial data fetched from regulator portals or mirrored on [Hugging Face (`mralbu/ogprod`)](https://huggingface.co/datasets/mralbu/ogprod) remains governed by each publisher's open-government-data terms:
  - **Brazil (`brazil`) — ANP:** Brazilian Federal Open Data Policy ([Decreto nº 8.777/2016](https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2016/decreto/d8777.htm)). Source: [ANP Dados Abertos](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/fase-de-desenvolvimento-e-producao) and `gishub.anp.gov.br`.
  - **Norway (`norway`) — Sodir:** [Norwegian Licence for Open Government Data (NLOD) 2.0](https://data.norge.no/nlod/en/2.0). Contains data under NLOD 2.0 distributed by the Norwegian Offshore Directorate ([FactPages](https://factpages.sodir.no/) / [Open Data](https://www.sodir.no/en/facts/data-and-analyses/open-data/)), normalized and converted to canonical units/schemas by `ogprod`.
  - **Mexico (`mexico`) — SENER / CNIH (ex-CNH):** Mexican Federal Open Data Policy (*Datos Abiertos* / *Términos de Libre Uso MX*). Source: [SENER / CNIH SIH](https://sih-hidrocarburos.energia.gob.mx/) and [Mapa de Hidrocarburos](https://mapa-hidrocarburos.energia.gob.mx/) (incorporating data from CNIH/SENER and Petróleos Mexicanos).
  - **United Kingdom (`uk`) — NSTA:** [NSTA User Agreement](https://www.nstauthority.co.uk/site-tools/access-to-information/) (non-commercial reuse). Contains information provided by the North Sea Transition Authority and/or other third parties ([NSTA Production Data](https://www.nstauthority.co.uk/data-and-insights/data/themes/production/) & [NSTA Open Data Hub](https://open-data-ukcs-transition.hub.arcgis.com/)).
  - **Argentina (`argentina`) — Secretaría de Energía:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/). Source: [Producción de petróleo y gas por pozo — Capítulo IV](https://datos.gob.ar/dataset/energia-produccion-petroleo-gas-por-pozo-capitulo-iv) and [Yacimientos](https://datos.gob.ar/dataset/energia-produccion-hidrocarburos---yacimientos) (`datos.gob.ar` / Secretaría de Energía de la Nación), normalized and aggregated to field level by `ogprod`.
  - **Netherlands (`netherlands`) — NLOG (TNO):** [NLOG Disclaimer](https://www.nlog.nl/en/disclaimer) (free reuse and redistribution with attribution). Source: [NLOG Production & Injection Data](https://www.nlog.nl/en/production-and-injection-data) and [GDN GeoServices OGC WFS](https://www.nlog.nl/en/files-interactive-map) (TNO – Geological Survey of the Netherlands / Ministry of Climate Policy and Green Growth).

