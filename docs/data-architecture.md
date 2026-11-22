# ogprod v2 — Data & Storage Architecture

This document specifies how `ogprod` downloads, normalizes, stores, and exports public oil & gas production, injection, and geospatial data for both local analytics (Python / DuckDB / QGIS) and a static web visualization app (MapLibre GL JS + DuckDB-WASM + PMTiles).

Priority order of sources:
1. **Brazil — ANP** (first priority: 1941–2026 well-level production & injection, fields, wells, platforms/FPSOs)
2. **Norway — Sodir** (field-level production 1971–present, field polygons, facilities, wellbores)
3. **Mexico — SENER / ex-CNH SIH** (well- and field-level production + spatial layers)

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
│   geo/wells/source=<src>.parquet                   (GeoParquet 1.1)     │
│   geo/fields/source=<src>.parquet                  (GeoParquet 1.1)     │
│   geo/facilities/source=<src>.parquet              (GeoParquet 1.1)     │
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
- **WFS column projection:** GeoServer WFS queries request only the required attributes via `propertyName=...`, and parsers drop any unused upstream fields immediately.
- **`float32` rates & metrics + ZSTD:** All production/injection rates and physical dimensions (`water_depth_m`, `area_km2`, capacities) use `float32` (~7.2 significant digits — far finer than meter accuracy; DuckDB accumulates `SUM(FLOAT)` in `float64`), paired with ZSTD level-6 compression and dictionary-encoded strings.
- **Incremental refreshes via `manifest.json`:** Records `(url, sha256, bytes, ingested_at)` per source file so historical years aren't re-downloaded unless `--refresh` is requested.

Default local root: `$OGPROD_DATA` (fallback `$XDG_DATA_HOME/ogprod`, else `~/.local/share/ogprod`). Inside a repo checkout, `--data-dir ./data` overrides it (`data/` is gitignored).

---

## 2. Canonical Schemas (Layer 1)

### 2.1 Production & Injection Table (`store/production/source=<src>/year=<yyyy>/data.parquet`)

**Wide format** — one row per `(source, entity_type, entity_id, field_id, facility_id, period)`. All rates are **calendar-day averages** over the production month (`monthly_volume / days_in_month`), stored in `float32` with unit metadata attached to every Arrow field:

| Column | Arrow Type | Unit | Nullable | Description |
|---|---|---|---|---|
| `source` | `string` | — | no | Connector ID (`"anp"`, `"sodir"`, `"mexico"`) |
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
| `gas_prod` | `float32` | `Mm³/d` (`10⁶ m³/d`) | yes | Daily average total gas production (associated + non-associated) |
| `water_prod` | `float32` | `bbl/d` | yes | Daily average produced water |
| `water_inj` | `float32` | `bbl/d` | yes | Daily average water injection (secondary recovery + disposal) |
| `gas_inj` | `float32` | `Mm³/d` (`10⁶ m³/d`) | yes | Daily average hydrocarbon gas injection |
| `co2_inj` | `float32` | `Mm³/d` (`10⁶ m³/d`) | yes | Daily average CO₂ injection |

Design rules:
- **Gas unit clarity:** `gas_prod`, `gas_inj`, and `co2_inj` use **SI `Mm³/d` = million `m³/d` (`10⁶ m³/d`, key `"e6m3/d"`)**. Note that ANP CSV headers write `Mm³` for Brazilian *Mil m³* (`10³ m³`), so ANP gas values are divided by `1,000 × days_in_month` on parse.
- **`NULL` vs `0.0`:** `NULL` means unreported; `0.0` means reported zero.
- **No double-counted rollups in Layer 1:** Well-level sources (ANP) store well rows carrying `field_id` and `facility_id`; field- and platform-level rates sum cleanly in SQL or Layer 2 `rollups/`. Field-level-only sources (Sodir) store `entity_type = "field"`.
- **Upsert semantics:** Regulators restate prior months. Writing a batch replaces stored rows matching `(source, entity_type, entity_id, field_id, facility_id, period)` within each affected `(source, year)` partition, sorted by `(entity_type, entity_id, period)` and written atomically via temp file + `os.replace`.

---

### 2.2 Spatial / Entity Tables (`store/geo/<kind>/source=<src>.parquet`)

All spatial tables are written as **GeoParquet 1.1** in **`EPSG:4326`**:
- Invalid polygons repaired with `make_valid()`.
- Coordinates validated within `|lon| <= 180`, `|lat| <= 90`.
- Missing geometries are allowed (`geometry = NULL`) so entities without coordinates still join for metadata.
- Only canonical columns are kept — unused GeoServer/CSV columns are dropped.
- Non-null geometries are sorted by **Hilbert curve distance** and written with a GeoParquet 1.1 **`bbox` covering struct** (`{xmin, ymin, xmax, ymax}`) and ZSTD level-6 compression.

#### `geo/fields/source=<src>.parquet` (`Polygon` / `MultiPolygon`)
`source`, `field_id`, `field_code`, `field_name`, `basin`, `region`, `environment`, `operator`, `status`, `primary_fluid`, `discovery_date`, `production_start_date`, `water_depth_m` (`float32`), `area_km2` (`float32`), `geometry`.

#### `geo/facilities/source=<src>.parquet` (`Point` — Platforms, FPSOs, UEPs)
`source`, `facility_id`, `facility_code`, `facility_name`, `facility_type`, `fields`, `basin`, `operator`, `status`, `start_year` (`int32`), `water_depth_m` (`float32`), `oil_capacity_bpd` (`float32`), `gas_capacity_mm3d` (`float32`), `export_system`, `geometry`.

#### `geo/wells/source=<src>.parquet` (`Point`)
`source`, `well_id`, `well_name`, `field_id`, `field_name`, `basin`, `region`, `environment`, `operator`, `status`, `lift_method`, `spud_date`, `completion_date`, `production_start`, `production_end`, `geometry`.

---

## 3. Source-by-Source Download & Mapping Plan

### 3.1 Priority 1 — Brazil (ANP)

ANP's [**Fase de Desenvolvimento e Produção**](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/fase-de-desenvolvimento-e-producao) portal publishes the entire **1941–2026** onshore + offshore archive directly as plain CSVs (requires a descriptive `User-Agent` header; default `python-httpx` gets 403 from `gov.br`).

| Dataset | Live Endpoint | Notes | Target Table |
|---|---|---|---|
| Well production & injection (`mar` 1941–2026, `terra` 1941–2026) | `.../arquivos-producao-de-petroleo-e-gas-natural-nacional/{pm,pt}/**/*.csv` (127 files, discovered from page HTML) | Unified 21-column CSV; newer files wrap headers in `[...]`. Drops unused exotic injection columns (`vapor`, `polímeros`, `nitrogênio`, `outros`). | `production` |
| Platforms / FPSOs (`lpo`) | `.../lpo/dados-abertos-plataformas-operacao.csv` (~20 KB) | Coordinates (`LATITUDE`/`LONGITUDE`), FPSO classification, water depth, oil (`bbl/d`) & gas (`Mil m³/d` → `Mm³/d`) capacities. | `geo/facilities` |
| Field polygons + metadata | WFS `BD_ANP:CAMPOS_PRODUCAO_SIRGAS` (`propertyName=...`) + `extracao-campo.csv` + `extracao-campo-devolvido.csv` | Polygons dissolved by field key; relinquished historical fields kept as metadata rows. | `geo/fields` |
| Well coordinates + lifecycle | `informacoes-sobre-pocos/situacao-pocos-1939-2026.csv` (~6.4 MB, single file with DMS coordinates, status, lift method, dates) — or WFS `BD_ANP:POCOS_SIRGAS` (`propertyName=...`) | Drops unused columns immediately; DMS coordinates parsed cleanly to `EPSG:4326` `Point`. | `geo/wells` |

**ANP data-quality rules:**
1. Merge pre-2005 / 2005-terra state cross-join duplicates preferring the row whose `Estado` matches the well name's state suffix; raise `SchemaError` if two non-zero values disagree by > 0.5% (and > 0.01 rounding tolerance).
2. Coalesce `Instalação = Não Informado` splits with empty volumes into the real installation row.
3. Never impute the `2003-07 .. 2005-06` gas gap (~5% of wells reporting gas); emit a warning and record in `manifest.json`.

### 3.2 Priority 2 — Norway (Sodir)
- Production: FactPages `field_production_monthly` CSV (1971–present, ~2 MB) → `entity_type = "field"`.
- Geo: FactMaps ArcGIS `MapServer/7100` (field polygons), fixed/moveable facilities, and wellbores.

### 3.3 Priority 3 — Mexico (SENER / ex-CNH SIH)
- Production & spatial layers via `sih-hidrocarburos.energia.gob.mx` and `mapa-hidrocarburos.energia.gob.mx` (audited live before implementing `sources/mexico.py`).

---

## 4. Web Viz Data Contract (Layer 2)

Built by `ogprod export-web` from Layer 1 for static hosting (Cloudflare R2 / S3 / GitHub Pages):
1. `tiles/ogprod.pmtiles` — MapLibre vector tiles (`fields`, `facilities`, `wells`).
2. `geo/{fields,facilities,wells}.parquet` — GeoParquet 1.1 with `bbox` + Hilbert sort.
3. `rollups/{country,basin,field,facility}_monthly.parquet` — pre-aggregated daily rates for instant DuckDB-WASM charts & FPSO utilization (`actual / oil_capacity_bpd`).
4. `well_series/` — bucketed Parquet for fast well-level drill-down over HTTP range requests.
