# Zenodo mirror for ANP historical files — design

Publish the 50 archived pre-2005 ANP production CSVs (see
[historical spec](2026-10-07-anp-historical-wayback-design.md)) as a
versioned Zenodo record. The `anp` historical fetch then falls back to it
whenever the Wayback Machine fails. The mirror hosts **unmodified publisher
bytes only**. Cleaning stays in `parse`, so the mirror and Wayback stay
interchangeable and checksum-verifiable.

## Why Zenodo

- Anonymous, direct HTTPS file URLs, so `fetch` can use the mirror without
  any credentials. Kaggle needs an account and API token to download, so it
  can't serve as a transparent fallback.
- DOI, versioned records, long-term hosting by CERN. 251 MB is far below
  the per-record limits (50 GB, 100 files).
- The licence is chosen at upload time.

Kaggle is still an option for visibility. It's not a mirror, and it's not
part of this spec.

## Licence and attribution

- No explicit licence for this dataset could be read (dados.gov.br's API
  returns 401 without a token). The federal framework permits
  redistribution with attribution:
  - [Decreto 8.777/2016](https://www2.camara.leg.br/legin/fed/decret/2016/decreto-8777-11-maio-2016-783082-publicacaooriginal-150347-pe.html)
    art. 2 defines open data as free to use, with credit to the source as the
    only requirement.
  - [Resolução CGINDA nº 2/2017](https://www.gov.br/governodigital/pt-br/legislacao/resolucaocginda22432017pdf.pdf)
    allows any use, including reproduction and distribution, with the source
    cited.
- The record is published under **CC-BY 4.0**. The creator is "Agência
  Nacional do Petróleo, Gás Natural e Biocombustíveis (ANP)" (organisation).
  The maintainer is credited as *contributor* (role: DataCurator), not as
  author.
- Before publishing, send a short courtesy note to faleconosco@anp.gov.br
  saying the files are being re-hosted with attribution. Proceed unless they
  object; their reply is not a blocker.

## Record contents

The original filenames are kept so the code's manifest serves both sources:

```
producao-terra-1941-1966.csv … producao-terra-2004-2sem.csv   (40 files)
producao-mar-1941-1979.csv  … producao-mar-2001-2004.csv     (10 files)
metadados-producao-oleo-gas.pdf   ANP's 2018 column dictionary (Wayback 20181101232314)
MANIFEST.csv                      one row per file (below)
README.md                         provenance, licence, known defects
```

- **No zip, no compression.** Each CSV is downloadable on its own, so
  `fetch(year=1985)` pulls one or two files instead of 251 MB. The bytes
  match the Wayback captures, so a single SHA-1 verifies both sources.
- `MANIFEST.csv` columns: `filename, environment (terra|mar), first_period,
  last_period, bytes, sha1_base32, sha1_hex, md5_hex, original_url,
  wayback_url`. Zenodo reports MD5 for every file and Wayback reports
  base32 SHA-1, so including both lets anyone check either source with
  standard tools.
- `README.md` states:
  - what the files are;
  - that ANP stopped publishing them (with dates);
  - that they're byte-identical to the 2018-08-27 Wayback captures;
  - the licence basis;
  - the known defects with counts: the state cross-join duplicates and the
    2003-07 … 2005-06 gas gap (from the
    [data-quality spec](2026-10-07-anp-data-quality-design.md));
  - a pointer to ogprod for parsed, cleaned access.
- Metadata: resource type *Dataset*; title "ANP well-level oil & gas
  production, Brazil, 1941–2004 (archived open-data CSVs)"; keywords; related identifiers: one `isDerivedFrom` pointing at the
  Wayback CDX listing (per-file links live in `MANIFEST.csv`) and one
  `isSupplementTo` pointing at the ogprod repository.

## Publishing (maintainer, manual and outward-facing)

A small maintainer script, `scripts/zenodo_publish_anp_historical.py`. It is
not part of the package, not imported by tests, and not run in CI.

1. Input: a directory with the 50 CSVs (normally downloaded fresh from
   Wayback by `fetch(historical=True)`, or the local archive) plus the
   metadata PDF.
2. Verifies every file against the code manifest (`_HISTORICAL_FILES`) and
   refuses to continue on any mismatch.
3. Generates `MANIFEST.csv` and `README.md`.
4. Uses the Zenodo REST API with `ZENODO_TOKEN` from the environment.
   `--sandbox` targets `sandbox.zenodo.org`, and the first run **must** be
   against the sandbox. The script creates the deposition, PUTs the files to
   the bucket, and sets metadata. It stops at a **draft**: publishing (which
   assigns the DOI and can't be undone) is a deliberate click in the Zenodo
   UI, or `--publish` on a second invocation after the draft has been
   reviewed.
5. Prints the record id, the concept DOI and the file URLs.

A new Zenodo version is only needed if a file is added or corrected. That
shouldn't happen, since the content is frozen publisher history. The code
pins the **version** record id, not the concept DOI, so a new version never
changes what `fetch` verifies.

## Fetch integration

Changes to the historical spec's fetch:

- `_HISTORICAL_MIRROR = "https://zenodo.org/records/<version-id>/files/{filename}?download=1"`.
  It stays `None` until the record is published.
- Order per file: Wayback (pinned capture) first, then the mirror. Reasoning:
  Wayback is the independent third-party source, and the mirror is ours.
  Both are verified with the same size + SHA-1 check, so the order only
  matters for availability.
- `fetch(..., source="wayback" | "mirror" | "auto")`, default `"auto"`, so
  users and tests can force one source.
- On fallback, `fetch` logs at INFO which source served each file.
- Errors: if both sources fail, raise one error listing both attempts (URL,
  status or checksum result) and pointing at docs/downloading.md's manual
  fallback (download from the record page by hand, then `load`).

## Tests

- `MockTransport` cases:
  - Wayback 503 three times, then the mirror serves correct bytes: succeeds,
    and the mirror URL is requested with the right filename.
  - Wayback serves wrong bytes: the checksum fails, the mirror is tried, and
    no bad file is left on disk.
  - Both sources fail: one error naming both.
  - `source="mirror"` skips Wayback.
  - `_HISTORICAL_MIRROR = None` with a Wayback failure: a clear error that
    no mirror is configured.
- Script: unit-test only the manifest/README generation (pure functions).
  The API calls are exercised manually against the sandbox.
- `test_live_smoke.py`: once the record exists, add a `HEAD` request for one
  mirror file next to the Wayback one.

## Docs

- docs/downloading.md: the historical sources, the mirror DOI, and the
  manual fallback.
- docs/design.md: one line under the ANP notes saying that fetch sources for
  pre-2005 files are Wayback, then Zenodo, both checksum-pinned.
- README / citation: suggest citing the Zenodo DOI and ANP when using pre-2005
  data.

## Order of work

1. Historical fetch + parse (Wayback only) together with the data-quality
   rules. This is fully usable without the mirror.
2. Sandbox upload, review, courtesy note to ANP.
3. Publish on zenodo.org, set `_HISTORICAL_MIRROR`, add the smoke test.

## Open questions

1. Should the record also carry the 2005–2020 archived files (the 2018–2019
   versions, which differ from today's zips) as a documented "as published
   then" snapshot? This spec says no: today's zips are authoritative for
   2005+, and adding them invites confusion.
2. Should the curator be the maintainer's personal Zenodo account or a
   project/community account? Zenodo communities can be added later.
