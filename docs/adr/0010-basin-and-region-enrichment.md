# 0010. Keep `basin` and `region` distinct; enrich Mexico fields via `geo_petro_prov` and fall back to `region` in Layer 2

Status: accepted

## Context

`ogprod`'s canonical Layer-1 schema defines two separate spatial grouping columns:

- `basin`: sedimentary basin / petroleum province (e.g. Brazil ANP `Bacia`, Argentina `Cuenca`).
- `region`: administrative or offshore sea/planning area (e.g. Brazil `Estado`, Mexico `Entidad`,
  Norway `fldMainArea`).

Brazil's ANP reports both natively on production and geo tables, but Norway and Mexico lacked
field-level basin coverage:

- **Mexico (SENER / ex-CNH):** neither `PRODUCCION_CAMPOS.csv` nor the field reserves shapefile
  (`cmp_cmp_rsv`, 477 fields) carries a basin attribute, though Mapa de Hidrocarburos publishes the
  official `geo_petro_prov` (*Provincias Petroleras con Recursos Prospectivos*, 13 polygons) layer
  on the same shapefile export API.
- **Norway (Sodir):** `field_production_monthly` and FactMaps layer `7100` carry only `fldMainArea`
  (`North Sea`, `Norwegian Sea`, `Barents Sea`), not a sedimentary basin column. Sodir's separate
  `FactMapsWGS84` layer `704` (*Structural elements*) contains 263 tectonic elements (grabens,
  highs, platforms, basins) with hierarchical `LEVEL2NAME` groupings, but lives on a different
  MapServer service and introduces editorial choices for boundary-straddling fields.

## Decision

1. **Keep `basin` and `region` strictly separate in Layer 1:**
   - Do not rename `basin` to `region` or copy sea areas into `basin`. Most current and planned
     jurisdictions (Brazil, Mexico, Argentina, US onshore, Canada) report genuinely distinct
     geological basins and administrative/planning regions.
   - **Mexico (`geo/fields`):** `Mexico.fetch_geo(GeoKind.FIELDS)` exports `geo_petro_prov`
     alongside `cmp_cmp_rsv`, and `_parse_fields_shp` assigns `basin` by largest summed polygon
     overlap in `EPSG:6372` (`geo.assign_by_largest_overlap`), matching 477/477 reserve-layer
     fields (~99.3% of 2020+ oil production). Cross-layer well fallbacks are intentionally omitted
     to keep `fields`, `facilities`, and `wells` ingestion independent.
   - **Norway (`geo/fields` & `geo/wells`):** leave `basin = NULL` in Layer 1 and keep
     `fldMainArea` / `wlbMainArea` in `region`, title-cased (`North Sea`, `Norwegian Sea`,
     `Barents Sea`) so fields and wells agree. If Sodir structural-element sub-basins are ever
     wanted later, they slot directly into `basin` with no schema change.
   - Layer-1 `production` partitions stay faithful to publisher rows (no geo backfill into
     historical production partitions).

2. **Resolve `basin` → `region` in Layer 2 and the web UI:**
   - `export_web._BASIN_MONTHLY_SQL` joins `fields` on `(source, field_id)` and groups by
     `COALESCE(p.basin, f.basin, p.region, f.region, 'Unassigned')`.
   - Search index entries and field map tiles expose `region` fallback when `basin` is absent.
   - The web explorer displays `basin ?? region` under `"Basin / Area"` / `"Basins & Areas"`
     without appending a literal `"Basin"` suffix to names.

## Consequences

- Zero breaking changes to the Layer-1 schema, public Python API, Hugging Face mirror layout,
  or URL state parameters.
- Mexico gets official petroleum-province attribution on every `cmp_cmp_rsv` field with one
  lightweight shapefile export on the existing API; historical fields absent from `cmp_cmp_rsv`
  (~0.7% of 2020+ oil) remain `'Unassigned'` in `basin_monthly`.
- Norway gets `North Sea` / `Norwegian Sea` / `Barents Sea` rollups in Layer 2 with zero extra
  HTTP requests or FactMaps layer-ID coupling.
- Direct DuckDB queries against Layer-1 `production` alone still see `basin = NULL` for Norway
  and Mexico field production unless joined to `fields`.
