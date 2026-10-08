# ogprod

[![CI](https://github.com/mralbu/ogprod/actions/workflows/ci.yml/badge.svg)](https://github.com/mralbu/ogprod/actions/workflows/ci.yml)

Organize public oil & gas production data.

Regulators around the world publish well- and field-level production — Brazil's
ANP, Argentina's Secretaría de Energía, Norway's Sodir, the UK's NSTA, the US
BSEE and state commissions, Canada's Petrinex, and more (see the full survey in
[docs/data-sources.md](docs/data-sources.md)). Each one uses its own file
format, column names, identifiers, and units. `ogprod` normalizes them into a
single canonical table so production from a Bakken well, a pré-sal well, and a
North Sea field can be analyzed side by side.

## Quickstart

Everything goes through one object, `ogprod.Store`: ingest a source, then
query it.

```python
import ogprod

ogprod.available()
# ['anp', 'argentina', 'sodir']

db = ogprod.Store()          # ~/.local/share/ogprod/store, or $OGPROD_STORE
db.ingest("sodir")           # fetch + parse + upsert, plus field ring fences
db.ingest("anp", year=2023, month=6)

oil = db.production(source="anp", product="oil", start="2023-06")
oil.groupby("field")["volume"].sum()   # one row per (well, month, product); always m³

fields = db.entities(source="sodir")   # GeoDataFrame, joins production on entity_id
db.coverage()                          # what the store holds, per source
db.sql("SELECT source, sum(volume) FROM production GROUP BY source")

ogprod.convert(1.0, "m3", "bbl")       # oilfield units at the edge
```

`ingest` resolves publishers' overlapping files for you (ANP's pré-sal file
restates part of the mar file; concatenating them by hand double-counts), and
re-running it picks up restated history. Entities (well coordinates, field
ring fences) come in on a source's first ingest; `refresh=True` re-downloads
them. Files you downloaded yourself go in with `db.ingest("anp", files=[...])`.

The same from the shell (`just ingest ...` in this repo):

```sh
python -m ogprod ingest sodir
python -m ogprod ingest anp --year 2023 --month 6
python -m ogprod coverage
```

The store is plain hive-partitioned Parquet (GeoParquet for entities), so
DuckDB, pandas or QGIS can also open the files directly.

Worked examples live in [notebooks/](notebooks/) — one per source, from fetch
to charts (`uv run --group notebooks jupyter lab` to run them). The canonical
schema, unit handling, and how to add a new source connector are documented in
[docs/design.md](docs/design.md).

## Built-in sources

| Connector | Publisher | Granularity | Notes |
|---|---|---|---|
| `anp` | ANP (Brazil) | well, monthly | Semicolon/comma-decimal CSVs; handles `Mm³` and `bbl/dia` rate columns |
| `argentina` | Secretaría de Energía (Argentina) | well, monthly | "Capítulo IV" CSVs, incl. the unconventional (Vaca Muerta) dataset |
| `sodir` | Sodir (Norway) | field, monthly | FactPages `field_production_monthly` table, back to 1971 |

Under the store, each connector (`ogprod.sources.get(name)`) separates
`fetch` (network: download the publisher's raw files) from `parse` (pure:
normalize one raw file, works on files downloaded by any means). See [docs/downloading.md](docs/downloading.md) for each source's
download options and caveats — e.g. ANP bulk files stop at 2023; newer Brazil
data is only in ANP's interactive CDP app, and ANP's own well-level gas is
incomplete for 2003-07..2005-06 (`ogprod.sources.anp.KNOWN_GAPS`; ogprod warns,
never imputes). The survey of ~15 further candidate
sources is in [docs/data-sources.md](docs/data-sources.md).

## Development

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/)
(`uv tool install rust-just`).

```sh
just sync        # install dependencies
just fetch ...   # download raw files into data/, e.g. `just fetch sodir`
just ingest ...  # fetch + parse + upsert into the store, e.g. `just ingest sodir`
just test        # pytest
just lint        # ruff check + format check
just fmt         # auto-format
just typecheck   # ty
just check       # lint + typecheck + test (what CI should run)
```

Project layout:

```
src/ogprod/
  schema.py        # canonical production + entities schemas, validation
  units.py         # volume unit conversion (everything stored in m³)
  store.py         # Store: ingest pipeline, Parquet/GeoParquet upsert, DuckDB queries
  __main__.py      # `python -m ogprod ingest|coverage|fetch ...` CLI
  sources/
    base.py        # Source ABC, registry, canonical-frame/entities builders
    anp.py         # Brazil, well-level (+ well cadastre entities)
    argentina.py   # Argentina, well-level (+ wells listing entities)
    sodir.py       # Norway, field-level (+ FactMaps ring-fence entities)
tests/             # pytest suite with fixture files mirroring real formats
notebooks/         # executed example notebooks, one per source
docs/
  downloading.md   # where to download each source's files, and how to load them
  data-sources.md  # research: who publishes production data worldwide
  design.md        # architecture and how to add a connector
```
