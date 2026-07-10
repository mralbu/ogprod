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
conventions drift) and extracts the CSVs; 2024+ exists only in the interactive
CDP app, so `fetch` raises a pointed error for those years. Note the pré-sal
file is a subset of the mar file — concatenating all three double-counts.

**`argentina` (well-level).** "Capítulo IV" CSVs: comma-separated, dot
decimals, stable lowercase column names (`idpozo`, `anio`, `mes`, `prod_pet`,
`prod_gas`, `prod_agua`). Oil/water in m³, gas in thousands of m³. `sigla` is
the human-readable well code; `idpozo` is the stable numeric ID. `fetch`
resolves the per-year CSV through the portal's CKAN API (`package_show`), so
no file URL is hardcoded; full-year files run 100–300 MB.

**`sodir` (Norway, field-level).** FactPages `field_production_monthly` CSV:
oil/NGL/condensate/water in millions of Sm³, gas in billions of Sm³. The
NPDID (`prfNpdidInformationCarrier`) is the stable entity ID; the oil
equivalent column (`prfPrdOeNetMillSm3`) is derived, not a product, and is
ignored. `fetch` hits the FactPages report-server CSV export directly — one
fixed URL returns the whole table (~2 MB).

### Format caveat

Fixture files in `tests/data/` mirror the publishers' real formats, and each
connector's `fetch`+`parse` was verified end-to-end against live downloads in
July 2026 (with totals cross-checked against published country figures). If a
publisher drifts a header again, extend the alias tables at the top of the
connector (`_META_ALIASES`, `_VOLUME_COLUMNS`) — that's the designated
extension point.

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
