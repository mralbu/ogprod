# BSEE OGOR-A connector — design

Add a `bsee` connector for US federal offshore (Gulf of Mexico + Pacific OCS)
well-level production from BSEE's OGOR-A bulk files, plus well-point entities
from the BSEE borehole file. Follows the existing `Source` pattern; no schema
or store changes.

All endpoints and formats below were verified empirically on 2026-07-11
(downloads of `ogora1996delimit.zip`, the current-year `ogoradelimit.zip`,
and `BoreholeRawData.zip`).

## Decisions (user-approved)

1. **Entity = 12-digit API well number** (`API_WELL_NUMBER` = 10-digit API +
   2-digit sidetrack suffix). OGOR-A reports per *completion*; `parse` sums
   completions to `(API, period, field, product)` grain. Completion/interval
   detail stays recoverable from the raw file only (design.md goal #2 —
   raw files are the trace-back path).
2. **Zero-volume rows are dropped.** ~40% of rows are status rows (all
   volumes zero); a shut-in month is a missing row, matching the other
   connectors' semantics.
3. **Entities = well points only.** BOEM lease rectangles are administrative,
   not geological, and BSEE publishes no field polygons; lease number and
   field code remain metadata columns on production rows.

## Production

### fetch

- Per-year zips, 1996–present:
  `https://www.data.bsee.gov/Production/Files/ogora{YYYY}delimit.zip`;
  the current year is the rolling
  `https://www.data.bsee.gov/Production/Files/ogoradelimit.zip`, regenerated
  bi-monthly. Historical zips are also regenerated (all carry fresh
  Last-Modified) — **history restates**, which the store's upsert absorbs.
- `fetch("bsee", dest, year=2024)` downloads one year and extracts the
  `.txt` flat into `dest`; `fetch("bsee", dest)` downloads the whole
  1996–current archive, extracting into per-year subdirectories (mirrors
  ANP's multi-zip layout). ~100 MB total uncompressed for the full archive.
- Plain HTTPS GET, no auth/rate limits; injectable `httpx.Client` as usual.

### parse

Input: one extracted `ogora*.txt`. Comma-delimited, **no header row**, every
field double-quoted, ASCII, CRLF; fields are space-/zero-padded inside the
quotes. Fixed 19-column layout (from BSEE's "Field Definitions for OGORA"
page, recorded in the module docstring):

| # | Column | Use |
|---|--------|-----|
| 1 | LEASE_NUMBER | metadata (kept out of canonical frame v1) |
| 2 | COMPLETION_NAME | aggregation input only |
| 3 | PRODUCTION_DATE | `period` (YYYYMM → month-start) |
| 4 | DAYS_ON_PROD | ignored |
| 5 | PRODUCT_CODE | ignored (volumes are explicit columns) |
| 6 | MON_O_PROD_VOL | oil, **bbl** → m³ |
| 7 | MON_G_PROD_VOL | gas, **Mcf** → m³ |
| 8 | MON_WTR_PROD_VOL | water, **bbl** → m³ |
| 9 | API_WELL_NUMBER | `entity_id` (12-digit, stripped) |
| 10 | WELL_STAT_CD | ignored (status lives on entities) |
| 11 | AREA_BLOCK | `region` (area code portion, e.g. `WD`) |
| 12 | OPERATOR_NUM | ignored |
| 13 | SORT_NAME | `operator` |
| 14 | FIELD_NAME_CODE | `field` (source-native code, e.g. `WD030`) |
| 15 | INJECTION_VOLUME | ignored |
| 16 | PROD_INTERVAL_CD | aggregation input only |
| 17 | FIRST_PROD_DATE | ignored |
| 18 | UNIT_AGT_NUMBER | ignored |
| 19 | UNIT_ALOC_SUFFIX | ignored |

Steps: read with the fixed column list → strip whitespace on all string
fields → period from YYYYMM → convert volumes with `units.py`
(bbl and Mcf = thousand scf; exact ISO factors already in `units.py`) →
melt to long → drop zero volumes → **groupby
`(entity_id, period, field, product)` summing volume**, taking first
non-null operator/region within each group → `base.build_frame`.
`entity_name` = the API number (BSEE assigns no well name in OGOR-A;
the borehole file carries `WELL_NAME` on the entities side).
`basin` = None (not reported). `entity_type` = well.

Rejection: a file whose rows don't have 19 columns, or whose
PRODUCTION_DATE column isn't YYYYMM, raises the usual "not an OGOR-A
file" `ValueError` naming the source.

Grain note: the verified-unique raw key is
`(LEASE_NUMBER, COMPLETION_NAME, PRODUCTION_DATE, API_WELL_NUMBER,
PROD_INTERVAL_CD, PRODUCT_CODE)`; after aggregation the canonical frame is
unique on `(entity_id, period, field, product)`, which matches the store's
upsert key.

## Entities

### fetch_entities

`https://www.data.bsee.gov/Well/Files/BoreholeRawData.zip` (~3.5 MB,
regenerated daily). Extracts `mv_boreholes_all.txt`.

### parse_entities

Quoted CSV **with header** (~57k rows). `_ENTITY_ALIASES` table absorbs
header drift. Mapping:

- `entity_id` = `API_WELL_NUMBER` (12-digit — same key as production)
- `entity_name` = `WELL_NAME` + `WELL_NAME_SUFFIX` (space-joined, stripped)
- `geometry` = Point(`SURF_LONGITUDE`, `SURF_LATITUDE`), decimal degrees
  ≈ EPSG:4326; missing/blank coords → null-geometry row (invariant)
- `status` = `BOREHOLE_STAT_CD`; `operator` = `COMPANY_NAME`;
  `region` = `REGION_CODE`; `field`/`basin` = None (not in this file)
- Dates in this file are unquoted `M/D/YYYY` — parsing must not choke on
  the mixed quoting.

Duplicate APIs, if any, fail `validate_entities` loudly (same watch-item
policy as ANP names).

## Store fit

No changes. Year = natural partition; BSEE's bi-monthly full regeneration
is the restatement case the upsert key
`(source, entity_id, period, product, field)` was built for.

## CLI / registry

`@base.register` + import in `sources/__init__.py`; `--year` already exists
on fetch/ingest (no `--month`). Update `_PER_SOURCE_OPTIONS` epilog:
bsee takes `--year` (1996–current) or no flags for the whole archive.

## Testing

- Fixtures in `tests/data/`: `bsee_ogora_sample.txt` — verbatim real lines
  (from the downloaded 1996 file) covering: two completions of the same API
  in one month (must sum), a sidetrack API (must stay distinct), an all-zero
  status row (must drop), padded ids (must strip); and
  `bsee_boreholes_sample.csv` — real header + a few rows including one with
  blank coordinates (null geometry).
- Tests: aggregation, unit conversion (bbl/Mcf → m³ exact factors), zero-row
  drop, padding strip, YYYYMM period, entities geometry + null-geometry row,
  production⋈entities join on a shared API, rejection of foreign files
  (ANP/argentina fixtures), fetch via `httpx.MockTransport` (year URL,
  current-year URL, zip extraction).
- Live verification before merge (repo practice): full-year live fetch,
  cross-check the year's GoM oil total against published BSEE/EIA figures,
  ingest into a store and confirm the entities join rate.
- Add one small bsee case to `tests/test_live_smoke.py` (current-year zip
  is ~400 KB — cheapest smoke of all sources).

## Docs

Per-source sections in `docs/downloading.md` (URLs, manual fallback via the
OGOR-A page, units caveat) and `docs/design.md` (per-connector notes +
entities notes); update `docs/data-sources.md` status, README source list,
CLAUDE.md source list, and `docs/roadmap.md` (mark BSEE done when merged).
