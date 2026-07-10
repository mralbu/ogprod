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
| `entity_id` | source-native identifier: ANP well code, Argentina `idpozo`, Sodir NPDID |
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

## Connectors

`sources/base.py` defines the `Source` ABC:

- `parse(raw) -> DataFrame` — **pure and mandatory.** Takes a path or file
  object of a raw file as published, returns a validated canonical frame.
- `fetch(dest_dir, **kwargs) -> Path` — **optional.** Downloads a raw file.
  Not yet implemented for the built-in connectors: this project was scaffolded
  in a sandbox whose network policy blocks the data portals, so download URLs
  and live file formats could not be verified. `fetch` raises
  `NotImplementedError` pointing at the publisher's download page instead of
  shipping an unverified URL.

`@base.register` adds a connector to the registry behind `ogprod.available()`,
`ogprod.get(name)`, and `ogprod.load(name, raw)`.

`base.build_frame(...)` is the shared assembler: connectors hand it metadata
series plus a `{Product: volumes_in_m3}` mapping and it melts, types, drops
missing volumes, and validates. Connectors stay ~80 lines of format knowledge.

### Per-connector notes

**`anp` (Brazil, well-level).** Semicolon-separated, comma decimals. Header
names have drifted over the years, so headers are matched after stripping
accents and case. The volume unit is read from the header's parenthesis —
`(m³)`, `(Mm³)` (thousand m³), and daily rates like `(bbl/dia)`, which are
multiplied by the days in the month before conversion. Multiple columns for
one product (e.g. associated + non-associated gas) are summed. Accepts either
`Ano`/`Mês` columns or a single `Período` column.

**`argentina` (well-level).** "Capítulo IV" CSVs: comma-separated, dot
decimals, stable lowercase column names (`idpozo`, `anio`, `mes`, `prod_pet`,
`prod_gas`, `prod_agua`). Oil/water in m³, gas in thousands of m³. `sigla` is
the human-readable well code; `idpozo` is the stable numeric ID.

**`sodir` (Norway, field-level).** FactPages `field_production_monthly` CSV:
oil/NGL/condensate/water in millions of Sm³, gas in billions of Sm³. The
NPDID (`prfNpdidInformationCarrier`) is the stable entity ID; the oil
equivalent column (`prfPrdOeNetMillSm3`) is derived, not a product, and is
ignored.

### Format caveat

Fixture files in `tests/data/` mirror the publishers' documented formats, but
the live portals were unreachable from the environment where this was built
(network policy). Before first production use of each connector, download one
real file and run it through `parse`; if a header drifted, extend the alias
tables at the top of the connector (`_META_ALIASES`, `_VOLUME_COLUMNS`) —
that's the designated extension point.

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

Good next candidates, roughly in order of effort (see data-sources.md):
BSEE OGOR-A (well, bulk delimited), Petrinex Alberta (well, CSV), NSTA PPRS
(field, CSV/ArcGIS), NLOG and the Danish Energy Agency (field, spreadsheets).

## Ingestion semantics (future work)

Publishers restate history: Petrinex ships amendments, Texas revises months
after the fact, ANP republishes. Any persistent store built on top of these
frames must upsert on `(source, entity_id, period, product)`, never append.
A `store` module (Parquet/DuckDB-backed) is the next architectural piece, but
it's deliberately out of scope for the first cut — the canonical frame is the
contract it will build on.
