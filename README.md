# ogprod

Organize public oil & gas production data.

Regulators around the world publish well- and field-level production — Brazil's
ANP, Argentina's Secretaría de Energía, Norway's Sodir, the UK's NSTA, the US
BSEE and state commissions, Canada's Petrinex, and more (see the full survey in
[docs/data-sources.md](docs/data-sources.md)). Each one uses its own file
format, column names, identifiers, and units. `ogprod` normalizes them into a
single canonical table so production from a Bakken well, a pré-sal well, and a
North Sea field can be analyzed side by side.

## Quickstart

```python
import ogprod

ogprod.available()
# ['anp', 'argentina', 'sodir']

# Download a source's raw file(s)...
paths = ogprod.fetch("anp", "data/", year=2023, month=1)

# ...and parse them into the canonical schema (or parse files you
# downloaded yourself)
frame = ogprod.load("anp", paths[0])

frame.columns
# source, entity_type, entity_id, entity_name, field, basin, region,
# operator, period, product, volume

# One row per (entity, month, product); volumes are always in m³
frame[frame["product"] == "oil"].groupby("field")["volume"].sum()

# Convert to oilfield units when you need them
ogprod.convert(1.0, "m3", "bbl")
```

The canonical schema, unit handling, and how to add a new source connector are
documented in [docs/design.md](docs/design.md).

## Built-in sources

| Connector | Publisher | Granularity | Notes |
|---|---|---|---|
| `anp` | ANP (Brazil) | well, monthly | Semicolon/comma-decimal CSVs; handles `Mm³` and `bbl/dia` rate columns |
| `argentina` | Secretaría de Energía (Argentina) | well, monthly | "Capítulo IV" CSVs, incl. the unconventional (Vaca Muerta) dataset |
| `sodir` | Sodir (Norway) | field, monthly | FactPages `field_production_monthly` table, back to 1971 |

Each connector separates `fetch` (network: download the publisher's raw files)
from `parse` (pure: normalize a raw file, works on files downloaded by any
means). See [docs/downloading.md](docs/downloading.md) for each source's
download options and caveats — e.g. ANP bulk files stop at 2023; newer Brazil
data is only in ANP's interactive CDP app. The survey of ~15 further candidate
sources is in [docs/data-sources.md](docs/data-sources.md).

## Development

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/)
(`uv tool install rust-just`).

```sh
just sync        # install dependencies
just test        # pytest
just lint        # ruff check + format check
just fmt         # auto-format
just typecheck   # ty
just check       # lint + typecheck + test (what CI should run)
```

Project layout:

```
src/ogprod/
  schema.py        # canonical long-format schema + validation
  units.py         # volume unit conversion (everything stored in m³)
  sources/
    base.py        # Source ABC, registry, canonical-frame builder
    anp.py         # Brazil, well-level
    argentina.py   # Argentina, well-level
    sodir.py       # Norway, field-level
tests/             # pytest suite with fixture files mirroring real formats
docs/
  downloading.md   # where to download each source's files, and how to load them
  data-sources.md  # research: who publishes production data worldwide
  design.md        # architecture and how to add a connector
```
