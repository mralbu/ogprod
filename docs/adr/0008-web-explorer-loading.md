# 0008. Web explorer data loading

Status: accepted

## Context

The static explorer (`site/`) must open instantly from static hosting, while well-level
history is far too large to ship up front.

## Decision

- Country, basin, field and facility series come from small precomputed rollups.
- Well series are queried with DuckDB-WASM, initialised on the first well click only,
  against just the remote Parquet partitions between the well's `first_year` and
  `last_year` (from the search index).
- Map layers use `ogprod.pmtiles` when `export-web` could run tippecanoe; otherwise the
  map falls back to GeoJSON point layers.
- A source's `fitBounds` uses its field geometries when it has any, so far-away facilities
  (e.g. Norwegian gas terminals in continental Europe) do not stretch the view; sources
  without usable field geometries use the union of all their layers.

## Consequences

Well charts have a first-click delay; tippecanoe is optional for development.
