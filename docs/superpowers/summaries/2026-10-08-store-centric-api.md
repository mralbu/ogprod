# Store-centric Python API — build summary

**Date:** 2026-10-08 · **Range:** `e51ce6b..784b16c` (PR #2)
**Spec:** [store-centric API](../specs/2026-10-08-store-centric-api-design.md) ·
**Open items:** [../../follow-ups.md](../../follow-ups.md#open-store-centric-api-review-2026-10-08-commit-784b16c)

## What was built

- **`ogprod.Store` is the single entry point.** Everything is ingested into
  a store and read back from it:

  ```python
  db = ogprod.Store()                    # $OGPROD_STORE, else ~/.local/share/ogprod/store
  db.ingest("anp", year=2023, month=6)   # fetch → parse → combine → upsert, + entities
  db.production(source="anp", product="oil")
  db.entities(source="anp"); db.sql("..."); db.coverage()
  ```

  The top level went from 17 names plus a second import (`ogprod.store`)
  to 5: `Store`, `available`, `convert`, `Product` and `SchemaError`. The
  connector layer stays in `ogprod.sources` (`get`, `Source`, `register`,
  builders), and the schema in `ogprod.schema`.
- **The ingest pipeline moved from the CLI into `Store.ingest`.** Python
  callers now get restatement handling, batching and failing-file names
  that used to be CLI-only. The CLI is a thin adapter: `ingest`
  (`--files`, `--no-entities`, `--refresh`), `coverage`, and `fetch`
  (raw files only).
- **`Source.restatement_order` makes ANP's pré-sal-after-mar rule
  explicit.** Before, it held only because `sorted()` happened to put
  `..._mar.csv` before `..._presal.csv`. It now also applies to files
  passed in with `files=`.
- **Entities on first ingest.** Entities come in when the store has none
  for that source yet, or with `refresh=True`, and are skipped for
  connectors with no entity download.
- **Typos fail.** Connector `fetch` declares its options explicitly (no
  `**kwargs`, base class included), so `fetch(..., yaer=2023)` raises
  `TypeError`. Before, it silently downloaded the whole 2005–2023 ANP
  archive. `@register` is now generic, so connector classes keep their
  own types.
- **Store location** is the user data dir, not `data/store`. It's called a
  *store*, not a cache, because publishers drop history and move
  endpoints, so the contents can't be assumed re-downloadable.

## Verification (live data, 2026-10-08)

- `just check` (ruff, ty, 143 tests) and `pytest -m live` (5 tests) pass.
  CI passes on Python 3.11 and 3.14.
- All three notebooks re-executed against the live publishers:
  - **ANP June 2023:** the real files are `2023_06_producao_Mar/Presal/Terra.csv`.
    The stored oil is 3.36 M bbl/d (terra + mar), against 5.91 for a naive
    sum of all three files, in line with Brazil's national output.
  - **Argentina 2026 (Jan–Sep):** 1.99M rows, 83k wells, 84k entities.
  - **Sodir:** full history, 142k rows and 142 ring fences.
- Store sizes: Argentina takes 2.8 B/row (5.5 MB for 2M rows), so its full
  ~20-year history is about 150 MB. A full prebuilt mirror would stay well
  under 1 GB.

## Review (standards + spec, after merge)

Both axes passed the core: the interface matches the spec, the error path
keeps flushed partitions, option validation happens before any network
access, and `coverage()` returns the documented columns on an empty store.
Findings, all now tracked in follow-ups.md:

- **Fixed in the docs pass:**
  - `design.md` still showed `fetch_entities(**kwargs)`, and its connector
    checklist didn't mention `restatement_order`.
  - `follow-ups.md` still referred to `ingest --entities`.
  - Three spec lines didn't match the code: where the
    `restatement_order` tests live, the live-smoke rewrite, and notebook
    02 keeping its raw CSVs.
- **Open:**
  - **A regression:** a failed ingest no longer lists the partitions
    already flushed. That fix was follow-up 7 of the July review.
  - An empty entity download fails after production is written.
  - Entity support is detected by checking whether `fetch_entities` is
    overridden, not by the `NotImplementedError` contract.
  - Smaller smells: `_environment_rank` is uncommented,
    `Store.production` duplicates the filter signature, and the CLI
    declares the same arguments once per subcommand.

## Decisions worth remembering

- **One way in, one way out.** `ingest` returns an `IngestResult`, never
  the frame. Returning the frame would bring back a second read path
  next to `production()`.
- **Store-centric removed a whole layer.** A store-less `read()` would
  have needed its own cache, freshness rules and restatement handling.
  With the store as the only path, re-running `ingest` is the refresh,
  and the parsed Parquet is the cache.
- **No f16/f32 for a future mirror.** f16 overflows: 4% of ANP volumes and
  32% of Sodir volumes became infinite. f32 saved at most 20% and breaks
  exact cross-checks of totals. zstd alone saves 20–25% losslessly.
- **The mirror is a second `ingest` origin, not a new interface.** The
  store's hive layout can serve directly as the distribution format
  (GitHub Release assets or Hugging Face, not files committed to git or
  LFS). It gets its own spec.

## Next

1. Fix the three open correctness items: flushed partitions on failure,
   empty entity download, explicit entity capability.
2. Spec the prebuilt-data mirror (`ingest(..., origin="mirror")`).
3. Historical ANP (Wayback fetch, old-format parse), as before. It now
   lands through `Store.ingest` without further interface changes.
