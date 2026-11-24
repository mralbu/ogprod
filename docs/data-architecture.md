# ogprod v2 — Data & Storage Architecture

This document specifies how `ogprod` downloads, normalizes, stores, and exports public oil & gas production, injection, and geospatial data for both local analytics (Python / DuckDB / QGIS) and a static web visualization app (MapLibre GL JS + DuckDB-WASM + PMTiles).

Priority order of built-in sources:
1. **Brazil — ANP** (first priority: 1941–2026 well-level production & injection, fields, wells, platforms/FPSOs)
2. **Norway — Sodir** (field-level production 1971–present, field polygons, facilities, wellbores)
3. **Mexico — SENER / ex-CNH SIH** (well- and field-level production + spatial layers)

For the broader global survey of candidate countries, regulators, spatial layers, and expansion trade-offs, see [data-sources.md](data-sources.md).

---

## 1. Space-Efficient Data Pipeline

```text
Regulator Portals (CSV / WFS / GeoJSON — requesting only needed columns where supported)
        │
        ▼  `ogprod ingest <source>` (stream to temp, parse & project immediately, discard raw by default)
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 1 — Canonical Store  (data/store/)                                │
│   manifest.json                                    (URLs, SHA-256, cov) │
│   production/source=<src>/year=<yyyy>/data.parquet (wide, float32, ZSTD)│
│   geo/wells/<src>.parquet                          (GeoParquet 1.1)     │
│   geo/fields/<src>.parquet                         (GeoParquet 1.1)     │
│   geo/facilities/<src>.parquet                     (GeoParquet 1.1)     │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼  `ogprod export-web` (read-only derived extracts for static hosting)
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 2 — Web Viz Artifacts  (data/web/)                                │
│   tiles/ogprod.pmtiles          (MapLibre vector tiles: fields/fac/well)│
│   geo/{fields,facilities,wells}.parquet  (GeoParquet 1.1 + bbox)        │
│   rollups/{country,basin,field,facility}_monthly.parquet                │
│   well_series/source=<src>/bucket=<00..ff>.parquet  (for DuckDB-WASM)   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Space-saving choices

- **No permanent raw hoard by default:** `ingest` streams remote files to a temporary directory, projects strictly the canonical columns, writes compressed Parquet/GeoParquet, and deletes the temporary raw file (`--keep-raw` is available if you want raw files retained for inspection).
- **WFS / ArcGIS column projection:** GeoServer WFS queries request only the required attributes via `propertyName=...`, FactMaps ArcGIS queries use `outFields=...`, and parsers drop any unused upstream fields immediately.
- **`float32` rates & metrics + ZSTD:** All production/injection rates and physical dimensions (`water_depth_m`, `area_km2`, capacities) use `float32` (~7.2 significant digits — far finer than meter accuracy; DuckDB accumulates `SUM(FLOAT)` in `float64`), paired with ZSTD level-6 compression and dictionary-encoded strings.
- **Incremental refreshes via `manifest.json`:** Records `(url, sha256, bytes, ingested_at)` and publisher attribution (`country`, `country_code`, `publisher`, `docs_url`) per source so historical years aren't re-downloaded unless `--refresh` is requested.

Default local root: `$OGPROD_DATA` (fallback `$XDG_DATA_HOME/ogprod`, else `~/.local/share/ogprod`). Inside a repo checkout, `--data-dir ./data` overrides it (`/data/` is gitignored).

---

## 2. Canonical Schemas (Layer 1)

### 2.1 Production & Injection Table (`store/production/source=<src>/year=<yyyy>/data.parquet`)

**Wide format** — one row per `(source, entity_type, entity_id, field_id, facility_id, period)`. All rates are **calendar-day averages** over the production month (`monthly_volume / days_in_month`), stored in `float32` with unit metadata attached to every Arrow field:

| Column | Arrow Type | Unit | Nullable | Description |
|---|---|---|---|---|
| `source` | `string` | — | no | Country / jurisdiction slug (`"brazil"`, `"norway"`, `"mexico"`; future sub-national sources like `"us-gom"`) |
| `entity_type` | `string` | — | no | Reporting grain: `"well"` or `"field"` |
| `entity_id` | `string` | — | no | Source-native identifier (`Poço` for ANP, `NPDID` for Sodir) |
| `entity_name` | `string` | — | no | Human-readable entity name |
| `field_id` | `string` | — | no | Normalized field key (joins `geo/fields`; `""` if unreported) |
| `field_name` | `string` | — | yes | Human-readable field name |
| `facility_id` | `string` | — | no | Normalized platform/FPSO/station key (joins `geo/facilities`; `""` if unreported) |
| `facility_name` | `string` | — | yes | Human-readable installation name (e.g. `FPSO ALMIRANTE BARROSO`) |
| `basin` | `string` | — | yes | Sedimentary basin |
| `region` | `string` | — | yes | State / province / sea area |
| `environment` | `string` | — | yes | `"onshore"` or `"offshore"` |
| `operator` | `string` | — | yes | Operator name (when reported on production rows) |
| `period` | `date32` | — | no | First day of the production month (`YYYY-MM-01`) |
| `oil_prod` | `float32` | `bbl/d` | yes | Daily average crude oil production |
| `condensate_prod` | `float32` | `bbl/d` | yes | Daily average condensate production |
| `ngl_prod` | `float32` | `bbl/d` | yes | Daily average NGL production (e.g. Sodir) |
| `gas_prod` | `float32` | `e3m3/d` (`10³ m³/d`) | yes | Daily average total gas production (associated + non-associated) |
| `water_prod` | `float32` | `bbl/d` | yes | Daily average produced water |
| `water_inj` | `float32` | `bbl/d` | yes | Daily average water injection (secondary recovery + disposal) |
| `gas_inj` | `float32` | `e3m3/d` (`10³ m³/d`) | yes | Daily average hydrocarbon gas injection |
| `co2_inj` | `float32` | `e3m3/d` (`10³ m³/d`) | yes | Daily average CO₂ injection |

Design rules:
- **Gas unit clarity:** `gas_prod`, `gas_inj`, and `co2_inj` use **`e3m3/d`** (`10³ m³/d`, thousand `m³/day`). We avoid bare `Mm³/d` (ambiguous internationally: `10³ m³` in Brazil vs `10⁶ m³` in SI/Europe) and `km³/d` (cubic kilometer = `10⁹ m³` in SI). ANP's `Mm³` CSV columns (*Mil m³*) are already in `10³ m³`, so parsing simply divides by calendar `days_in_month`.
- **`NULL` vs `0.0`:** `NULL` means unreported; `0.0` means reported zero.
- **No double-counted rollups in Layer 1:** Well-level sources (Brazil/ANP) store well rows carrying `field_id` and `facility_id`; field- and platform-level rates sum cleanly in SQL or Layer 2 `rollups/`. Field-level-only sources (Norway/Sodir) store `entity_type = "field"`.
- **Upsert semantics:** Regulators restate prior months. Writing a batch replaces stored rows matching `(source, entity_type, entity_id, field_id, facility_id, period)` within each affected `(source, year)` partition, sorted by `(entity_type, entity_id, period)` and written atomically via temp file + `Path.replace`.

---

### 2.2 Spatial / Entity Tables (`store/geo/<kind>/<source>.parquet`)

All spatial tables are written as **GeoParquet 1.1** in **`EPSG:4326`**:
- Invalid polygons repaired with `make_valid()`.
- Coordinates validated within `|lon| <= 180`, `|lat| <= 90`.
- Missing geometries are allowed (`geometry = NULL`) so entities without coordinates still join for metadata.
- Only canonical columns are kept — unused GeoServer/CSV columns are dropped.
- Non-null geometries are sorted by **Hilbert curve distance** and written with a GeoParquet 1.1 **`bbox` covering struct** (`{xmin, ymin, xmax, ymax}`) and ZSTD level-6 compression.

#### `geo/fields/<source>.parquet` (`Polygon` / `MultiPolygon`)
`source`, `field_id`, `field_code`, `field_name`, `basin`, `region`, `environment`, `operator`, `status`, `primary_fluid`, `discovery_date`, `production_start_date`, `water_depth_m` (`float32`), `area_km2` (`float32`), `geometry`.

#### `geo/facilities/<source>.parquet` (`Point` — Platforms, FPSOs, UEPs)
`source`, `facility_id`, `facility_code`, `facility_name`, `facility_type`, `fields`, `basin`, `operator`, `status`, `start_year` (`int32`), `water_depth_m` (`float32`), `oil_capacity_bpd` (`float32`), `gas_capacity_e3m3d` (`float32`), `export_system`, `geometry`.

#### `geo/wells/<source>.parquet` (`Point`)
`source`, `well_id`, `well_name`, `field_id`, `field_name`, `basin`, `region`, `environment`, `operator`, `status`, `lift_method`, `spud_date`, `completion_date`, `production_start`, `production_end`, `geometry`.

---

## 3. Source-by-Source Download & Mapping Plan

### 3.1 Priority 1 — Brazil (ANP)

ANP's [**Fase de Desenvolvimento e Produção**](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/fase-de-desenvolvimento-e-producao) portal publishes the entire **1941–2026** onshore + offshore archive directly as plain CSVs (requires a descriptive `User-Agent` header; default `python-httpx` gets 403 from `gov.br`).

| Dataset | Live Endpoint | Notes | Target Table |
|---|---|---|---|
| Well production & injection (`mar` 1941–2026, `terra` 1941–2026) | `.../arquivos-producao-de-petroleo-e-gas-natural-nacional/{pm,pt}/**/*.csv` (127 files, discovered from page HTML) | Unified 21-column CSV; newer files wrap headers in `[...]`. Drops unused exotic injection columns (`vapor`, `polímeros`, `nitrogênio`, `outros`). | `production` |
| Platforms / FPSOs (`lpo`) | `.../lpo/dados-abertos-plataformas-operacao.csv` (~20 KB) | Coordinates (`LATITUDE`/`LONGITUDE`), FPSO classification, water depth, oil (`bbl/d`) & gas (`Mil m³/d` = `e3m3/d`) capacities. | `geo/facilities` |
| Field polygons + metadata | WFS `BD_ANP:CAMPOS_PRODUCAO_SIRGAS` (`propertyName=...`) + `extracao-campo.csv` + `extracao-campo-devolvido.csv` | Polygons dissolved by field key; relinquished historical fields kept as metadata rows. | `geo/fields` |
| Well coordinates + lifecycle | `informacoes-sobre-pocos/situacao-pocos-1939-2026.csv` (~6.4 MB, single file with DMS coordinates, status, lift method, dates) — or WFS `BD_ANP:POCOS_SIRGAS` (`propertyName=...`) | Drops unused columns immediately; DMS coordinates parsed cleanly to `EPSG:4326` `Point`. | `geo/wells` |

**ANP data-quality rules:**
1. Merge pre-2005 / 2005-terra state cross-join duplicates preferring the row whose `Estado` matches the well name's state suffix; raise `SchemaError` if two non-zero values disagree by > 0.5% and > 0.5 bbl/d (see [ADR 0004](adr/0004-duplicate-production-keys.md)).
2. Coalesce `Instalação = Não Informado` splits with empty volumes into the real installation row.
3. Never impute well-level rows for the `2003-07 .. 2005-06` gas gap (~5% of wells reporting gas); emit a warning in Layer 1, backfill `country_monthly` from ANP's official `ppgn-el` aggregate series, and mask `basin_monthly` in Layer 2 (see [ADR 0009](adr/0009-anp-gas-gap-handling.md)).

### 3.2 Priority 2 — Norway (Sodir)

- **Production:** FactPages `field_production_monthly` CSV (1971–present, single ~2 MB file): oil, condensate, NGL, produced water (`Mill Sm³` → `bbl/d`), and gas (`Bill Sm³` → `e3m3/d`). Grain: `entity_type = "field"`, `entity_id = NPDID`.
- **Geo (FactMaps ArcGIS DataService, requesting only needed attributes via `outFields=` and paginating with `resultOffset`):**
  - `geo/fields`: layer `7100` (`field`, 142 fields, dissolved by `fldNpdidField`).
  - `geo/facilities`: layer `6000` (`facility`, ~1,229 platforms/FPSOs/subsea templates with water depth & startup year).
  - `geo/wells`: layer `5000` (`wellbore`, ~9,884 wellbores paginated in chunks of 2,000, linked to `field_id`).

### 3.3 Priority 3 — Mexico (SENER / ex-CNH SIH)

- **Production (`sih-hidrocarburos.energia.gob.mx/downloads/`):**
  - `PRODUCCION_CAMPOS.csv` (1960–present, ~18 MB, default on `ingest mexico`): field-level daily rates — oil, condensate, water (`Mbd` = thousand bbl/d → `bbl/d`), associated + non-associated gas (`MMpcd` = million scf/d → `e3m3/d`).
  - `PRODUCCION_POZOS.zip` / `POZOS_COMPILADO.csv` (1930–present, ~155 MB): well-level daily rates, auto-detected by `parse_production` when ingested via `--files`.
- **Geo (`mapa-hidrocarburos.energia.gob.mx` public shapefile export API):**
  - `geo/fields`: `cmp_cmp_rsv` (477 field polygons, `EPSG:4326`; drops 1P/2P/3P reserve columns), enriched with `basin` via area-weighted overlay against `geo_petro_prov` (Provincias Petroleras, `EPSG:6372`; see [ADR 0010](adr/0010-basin-and-region-enrichment.md)).
  - `geo/facilities`: `v_iftep_instalaciones` (1,363 platforms/installations, `EPSG:4326`).
  - `geo/wells`: 6 regional well layers (`v_zon_pozos_{burgos,tampico,ver,cuenca_sur,agu_som,agu_pro}`).

---

## 4. Web Viz Data Contract (Layer 2)

Built by `ogprod export-web` from Layer 1 for static hosting (Cloudflare R2 / S3 / GitHub Pages):
1. `tiles/ogprod.pmtiles` — MapLibre vector tiles (`fields`, `facilities`, `wells`).
2. `geo/{fields,facilities,wells}.parquet` — GeoParquet 1.1 with `bbox` + Hilbert sort.
3. `rollups/{country,basin,field,facility}_monthly.parquet` — pre-aggregated daily rates for instant DuckDB-WASM charts & FPSO utilization (`actual / oil_capacity_bpd`); `basin_monthly` falls back from `production.basin` to `fields.basin`, `production.region`, and `fields.region` (see [ADR 0010](adr/0010-basin-and-region-enrichment.md)).
4. `well_series/` — bucketed Parquet for fast well-level drill-down over HTTP range requests.
