# Downloading and loading data

`ogprod` downloads production files for you, parses them into the canonical
frame, and keeps the result in a local store (`Store.ingest`). This page is
the practical companion to the source survey in
[data-sources.md](data-sources.md) and the architecture in
[design.md](design.md): what ingest downloads per source, where to click when
you download by hand, and the per-source caveats.

## The workflow

```python
import ogprod

ogprod.available()                      # ['anp', 'argentina', 'sodir']

db = ogprod.Store()                     # ~/.local/share/ogprod/store, or $OGPROD_STORE
db.ingest("sodir")                      # download, parse, upsert (+ entities, first time)
frame = db.production(source="sodir")

# Files you downloaded by hand (e.g. a CDP export) go in the same way:
db.ingest("anp", files=["2023_01_producao_Mar.csv"], entities=False)
```

Parsing reads each file, matches its columns, converts every volume to m³,
drops rows with no volume, and runs `schema.validate`; ingest then resolves
overlapping files (ANP's pré-sal restates mar) and upserts. Frames have the
canonical columns documented in [design.md](design.md#canonical-schema).

Under the store, every connector splits two concerns (`sources/base.py`),
reachable through `ogprod.sources.get(name)` when you want raw files or a
single parsed file:

- **`parse(raw)`** — pure, always available. Takes a path or open file object
  of a file *as published* and returns a validated canonical frame.
- **`fetch(dest_dir, ...)`** — network. Downloads the raw file(s) into
  `dest_dir` and returns their paths (a list — ANP splits one month across
  several CSVs). Options (`year`, `month`) are explicit per connector; an
  unknown one raises `TypeError`.

```python
from ogprod import sources

paths = sources.get("sodir").fetch("data/")       # raw files only
frame = sources.get("sodir").parse(paths[0])      # one file, not stored
```

The CLI mirrors this (`just ingest ...` / `just fetch ...` inside this repo):

```sh
python -m ogprod ingest sodir                       # into the store
python -m ogprod ingest anp --year 2023 --month 1
python -m ogprod ingest anp --files a.csv b.csv     # files downloaded by hand
python -m ogprod coverage                           # what the store holds
python -m ogprod fetch argentina --year 2024        # raw files into data/, no ingest
python -m ogprod fetch sodir --entities --dest raw/ # raw entity file (FactMaps GeoJSON)
```

`fetch` prints the downloaded paths, one per line. `ingest` brings in each
source's entity geometry (well coordinates or field ring fences) on its first
run; `--refresh` re-downloads it and `--no-entities` skips it.

Not every source has entity geometry wired up the same way as production —
see the per-source notes below for what the entity download fetches and its
manual fallback.

All three fetch implementations were verified against live downloads in July
2026, with parsed totals cross-checked against published country figures.

## Per-source download

### `anp` — Brazil, well-level (monthly)

```python
db.ingest("anp", year=2023, month=1)   # monthly, 2021-2023
db.ingest("anp", year=2021)            # every month of a year
db.ingest("anp", year=2019)            # yearly, 2005-2020
db.ingest("anp")                       # everything, 2005-2023 (~70 MB download)
```

`fetch` scrapes the [gov.br open-data page](https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/producao-de-petroleo-e-gas-natural-por-poco)
for the bulk zip links (the filename convention has drifted year to year, so
links are matched by the numbers in them, not by a fixed pattern), downloads
the zip(s), and extracts their CSVs. A single-period fetch extracts flat into
`dest_dir`; multi-zip fetches (whole year, or everything) extract each period
into its own `<year>` / `<year>-<month>` subdirectory.

Caveats:

- **Data ends at 2023-12.** From 2024 on, ANP publishes well production only
  through the interactive [CDP application](https://cdp.anp.gov.br/) — there
  is no bulk file to download, and `fetch` raises an error saying so. Export a
  CSV from CDP by hand and ingest it with `db.ingest("anp", files=[...])`.
- **One month = several files.** Each zip bundles **terra** (onshore), **mar**
  (offshore) and **pré-sal** CSVs. The pré-sal file is a *subset* of the mar
  file: analyze it separately, but sum only terra + mar for country totals —
  concatenating all three double-counts pre-salt production.
- **Yearly archives can overlap themselves too.** Some yearly zips (e.g. 2012)
  contain per-month CSVs *and* whole-year aggregate CSVs (`2012_06_producao_mar.csv`
  alongside `2012_producao_mar.csv`). Load one granularity, not both, or every
  volume counts twice.
- **Gas is missing for 2003-07 … 2005-06.** ANP's own files report gas for
  only ~600 of ~8,300 wells in those months, so national gas totals there are
  ≈5% of reality. ogprod warns and leaves the data as published; see
  `ogprod.sources.anp.KNOWN_GAPS`.

The files are semicolon-separated with comma decimals and dot thousands
separators, and bulk files use a stacked multi-row header — `parse` handles
all of that (see [design.md](design.md#per-connector-notes)).

**Entities (well coordinates).**

```python
db.entities(source="anp")                          # ingested with production
paths = sources.get("anp").fetch_entities("data/")   # or the raw cadastre file
```

The gov.br-documented "Tabela de Poços" download page 404s; `fetch_entities`
instead queries ANP's GeoServer WFS directly (`BD_ANP:POCOS_SIRGAS`) for the
full cadastre (~31k wells) as CSV with a WKT point column. `entity_id` is the
well name (`Nome Poço`) — the cadastre has no separate stable well code.

Manual alternative: the yearly "poços públicos" CSVs on the same gov.br
open-data page (declassified wells only, a few hundred rows/year) work too —
`parse_entities` accepts either the WFS export or that format (explicit
`LATITUDE_BASE_DD`/`LONGITUDE_BASE_DD` columns).

### `argentina` — Secretaría de Energía, well-level (monthly)

```python
db.ingest("argentina", year=2024)
```

`fetch` asks the portal's CKAN API ([package_show](http://datos.energia.gob.ar/api/3/action/package_show?id=produccion-de-petroleo-y-gas-por-pozo))
for the per-year CSV resource, so no file URL is hardcoded. The resource is
matched by exact name (`Producción de Pozos de Gas y Petróleo – <year>`,
any dash); the `(DDJJ abiertas y cerradas)` variants are skipped, and when a
year is listed twice (2024 and 2025 are, as of October 2026, the newer copy
adding an `id` column over the same rows) the most recently created resource
wins. Full-year files are 100–300 MB; expect the download to take a minute.

Manual alternative: the dataset page is
[Producción de petróleo y gas por pozo (Capítulo IV)](http://datos.energia.gob.ar/dataset/produccion-de-petroleo-y-gas-por-pozo).
There is a parallel **unconventional** dataset (Vaca Muerta) with the same
layout — the connector parses both, but `fetch` currently resolves only the
per-year conventional+unconventional combined files (`Producción de Pozos de
Gas y Petróleo – <year>`).

Files are comma-separated, dot decimals, UTF-8, with stable lowercase column
names (`idpozo`, `anio`, `mes`, `prod_pet`, `prod_gas`, `prod_agua`). Oil/water
are in m³, gas in thousands of m³.

**Entities (well coordinates).**

```python
db.entities(source="argentina")                          # ingested with production
paths = sources.get("argentina").fetch_entities("data/")   # or the raw listing
```

The wells listing is not a separate CKAN package (the documented
"listado-de-pozos-cargados-por-empresas-operadoras" id 404s) — it's another
CSV resource, "Listado de pozos cargados por empresas operadoras", inside the
*same* `produccion-de-petroleo-y-gas-por-pozo` dataset used for production.
Coordinates are plain decimal degrees in `coordenadax`/`coordenaday`
(lon/lat), unlike production's `geojson` column. `operator` comes from
`idempresa`, a short company code (e.g. "YPF") — **not** directly comparable
to production's `empresa`; `clasificacion` (e.g. "EXPLOTACION") is the
closest analog to well status.

Manual alternative: same dataset page as production, above; download the
"Listado de pozos..." resource by hand.

### `sodir` — Norway (ex-NPD), field-level (monthly)

```python
db.ingest("sodir")   # whole history, one ~2 MB CSV
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

**Entities (field ring fences).**

```python
db.entities(source="sodir")                          # ingested with production
paths = sources.get("sodir").fetch_entities("data/")   # or the raw GeoJSON
```

There is no public zipped shapefile — the documented
`/downloads/shape/fldArea.zip` 404s. `fetch_entities` instead queries
FactMaps' ArcGIS DataService directly (layer 7100, "field") for GeoJSON
polygons in EPSG:4326. `parse_entities` keeps fields with no geometry yet
(newly approved, not delineated) as null-geometry rows, repairs
self-intersecting rings with `make_valid`, and dissolves each field's
several polygons into one; `entity_id` is the field NPDID.

Manual alternative: [FactMaps](https://factmaps.sodir.no/) lets you query
and export layer 7100 by hand if the API URL above ever moves.

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
def fetch(self, dest_dir, *, year=None, client=None):  # explicit options, no **kwargs
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with base.open_client(client) as http:
        return [base.download(http, VERIFIED_URL, dest_dir / "data.csv")]
```

Keep `fetch` and `parse` separate so parsing stays testable offline: `fetch`
returns paths, and `parse` (driven by `Store.ingest`) takes it from there. If
the publisher's files overlap, override `restatement_order` so the restating
file is parsed last. Accept a `client`
keyword and test the fetch against an `httpx.MockTransport` with canned
publisher responses (see `tests/test_fetch.py`); verify the URL and the
downloaded file end to end against the live portal before wiring it in.
