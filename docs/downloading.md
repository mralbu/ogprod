# Downloading and loading data

`ogprod` can download production files for you (`fetch`) and parse them into
the canonical frame (`load`). This page is the practical companion to the
source survey in [data-sources.md](data-sources.md) and the architecture in
[design.md](design.md): what `fetch` gets you per source, where to click when
you download by hand, and the per-source caveats.

## The workflow

Every connector splits two concerns (`sources/base.py`):

- **`parse(raw)`** — pure, always available. Takes a path or open file object
  of a file *as published* and returns a validated canonical frame.
- **`fetch(dest_dir, **kwargs)`** — network. Downloads the raw file(s) into
  `dest_dir` and returns their paths (a list — ANP splits one month across
  several CSVs).

```python
import ogprod

ogprod.available()                     # ['anp', 'argentina', 'sodir']

paths = ogprod.fetch("sodir", "data/")
frame = ogprod.load("sodir", paths[0])

# fetch is optional — load also takes files you downloaded by hand,
# as a path or an open file/buffer:
frame = ogprod.load("anp", "2023_01_producao_Mar.csv")
```

`load` reads the file, matches its columns, converts every volume to m³, drops
rows with no volume, and runs `schema.validate` before returning. The result
has the canonical columns documented in [design.md](design.md#canonical-schema).

There is also a CLI wrapper for downloading without opening Python
(`just fetch ...` inside this repo runs the same thing):

```sh
python -m ogprod fetch sodir                      # into data/ by default
python -m ogprod fetch argentina --year 2024
python -m ogprod fetch anp --year 2023 --month 1 --dest raw/
```

It prints the downloaded paths, one per line.

All three fetch implementations were verified against live downloads in July
2026, with parsed totals cross-checked against published country figures.

## Per-source download

### `anp` — Brazil, well-level (monthly)

```python
paths = ogprod.fetch("anp", "data/", year=2023, month=1)   # monthly, 2021-2023
paths = ogprod.fetch("anp", "data/", year=2019)            # yearly,  2005-2020
```

`fetch` scrapes the [gov.br open-data page](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/producao-de-petroleo-e-gas-natural-por-poco)
for the bulk zip links (the filename convention has drifted year to year, so
links are matched by the numbers in them, not by a fixed pattern), downloads
the zip, and extracts its CSVs.

Caveats:

- **Data ends at 2023-12.** From 2024 on, ANP publishes well production only
  through the interactive [CDP application](https://cdp.anp.gov.br/) — there
  is no bulk file to download, and `fetch` raises an error saying so. Export a
  CSV from CDP by hand and pass it to `load`.
- **One month = several files.** Each zip bundles **terra** (onshore), **mar**
  (offshore) and **pré-sal** CSVs. The pré-sal file is a *subset* of the mar
  file: analyze it separately, but sum only terra + mar for country totals —
  concatenating all three double-counts pre-salt production.

The files are semicolon-separated with comma decimals and dot thousands
separators, and bulk files use a stacked multi-row header — `parse` handles
all of that (see [design.md](design.md#per-connector-notes)).

### `argentina` — Secretaría de Energía, well-level (monthly)

```python
paths = ogprod.fetch("argentina", "data/", year=2024)
```

`fetch` asks the portal's CKAN API ([package_show](http://datos.energia.gob.ar/api/3/action/package_show?id=produccion-de-petroleo-y-gas-por-pozo))
for the per-year CSV resource, so no file URL is hardcoded. Full-year files
are 100–300 MB; expect the download to take a minute.

Manual alternative: the dataset page is
[Producción de petróleo y gas por pozo (Capítulo IV)](http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo).
There is a parallel **unconventional** dataset (Vaca Muerta) with the same
layout — the connector parses both, but `fetch` currently resolves only the
per-year conventional+unconventional combined files (`Producción de Pozos de
Gas y Petróleo – <year>`).

Files are comma-separated, dot decimals, UTF-8, with stable lowercase column
names (`idpozo`, `anio`, `mes`, `prod_pet`, `prod_gas`, `prod_agua`). Oil/water
are in m³, gas in thousands of m³.

### `sodir` — Norway (ex-NPD), field-level (monthly)

```python
paths = ogprod.fetch("sodir", "data/")   # whole history, one ~2 MB CSV
```

`fetch` hits the FactPages report-server CSV export for the
`field_production_monthly` table directly — one fixed URL returns the whole
table back to 1971.

Manual alternative: open [FactPages](https://factpages.sodir.no/), navigate
**Field → Table view → Production → Saleable → Monthly** and click **"Download
data"** (or use the [open-data](https://www.sodir.no/en/facts/data-and-analyses/open-data/)
full table exports).

Oil/NGL/condensate/water are in millions of Sm³, gas in billions of Sm³; the
field is identified by its stable NPDID (`prfNpdidInformationCarrier`) and
name.

## When a file fails to parse

The connectors reject files that aren't from the expected source (missing
required columns) and read units out of column headers. If a real download
fails or produces wrong numbers, it is almost always a **header that drifted**
from what the connector expects. The fix is to extend the alias/column tables
at the top of the connector module — that is the designated extension point:

- `anp.py`: `_META_ALIASES`, `_UNIT_ALIASES`, `_PRODUCT_PREFIXES`
- `argentina.py` / `sodir.py`: `_VOLUME_COLUMNS`, `_REQUIRED`

Add a small fixture mirroring the new header to `tests/data/` and a test, per
the checklist in [design.md](design.md#adding-a-connector).

## Adding automated fetch to a new connector

Override `fetch` on the `Source` subclass; use the shared helpers in
`sources/base.py`:

```python
def fetch(self, dest_dir, *, client=None, **kwargs):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with base.open_client(client) as http:
        return [base.download(http, VERIFIED_URL, dest_dir / "data.csv")]
```

Keep `fetch` and `parse` separate so parsing stays testable offline: `fetch`
returns paths, and `load`/`parse` take it from there. Accept a `client`
keyword and test the fetch against an `httpx.MockTransport` with canned
publisher responses (see `tests/test_fetch.py`); verify the URL and the
downloaded file end to end against the live portal before wiring it in.
