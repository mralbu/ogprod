# Follow-ups

Open findings from the storage-feature reviews (July 2026), ordered by
severity. Each was independently verified against the code — and where
noted, against live publisher data. None blocks current use; the first
five are silent-failure or whole-file-failure paths worth closing before
the store sees regular multi-source backfills.

## Correctness

1. **Argentina ids are float-promotion fragile** — `argentina.py` `parse`
   and `parse_entities` both do a bare `frame["idpozo"].astype("string")`.
   One blank `idpozo` cell promotes the column to float64 and every id in
   that file becomes `"50136.0"`, silently breaking every
   production⋈entities join (verified by repro). The fix already exists for
   Sodir (`sodir._npdid_string`: `to_numeric → Int64 → string` with a
   plain-string fallback); generalize it — probably into
   `base.build_frame`/`build_entities` so every current and future
   connector gets it.

2. **One dirty row aborts a whole Argentina parse** — `schema.validate`
   now rejects null periods, but `base.build_frame` only drops
   null-*volume* rows. A row with a blank `anio`/`mes` and a non-null
   volume yields `period=NaT` and the whole 100–300 MB file fails
   (verified by repro). Decide the policy: either drop null-period rows in
   `build_frame` (with a count in a warning) or keep the hard failure and
   document it. ANP is protected by its `_PERIOD_PATTERN` row filter;
   Argentina has no equivalent.

3. **Sodir silently drops fields whose NPDID is null** —
   `parse_entities`' `dissolve(by=entity_id)` inherits pandas'
   `dropna=True`, so a feature with a valid ring fence but a null
   `fldNpdidField` vanishes with no diagnostic (verified by repro). Either
   pass `dropna=False` and let validation report it, or drop explicitly
   with a named reason like the null-geometry filter one line above.

4. **`validate_entities` misdiagnoses blank ids as duplicates** — two rows
   with null `entity_id` collide under pandas' NaN-equal `duplicated()`
   semantics and fail the batch with a misleading "duplicate
   (source, entity_id)" error (verified by repro). Add an explicit
   null-`entity_id` check (like the null-period/source checks in
   `validate`) so the error names the real problem.

5. **Sodir drops null-geometry fields; the schema says keep them** —
   `docs/design.md` states missing geometry is a row with
   `geometry = None`, not an error, and ANP/Argentina honor that. Sodir's
   `parse_entities` filters those features out entirely, so a
   not-yet-delineated field can't even be joined for metadata. Align the
   connector with the documented invariant (keep the row, null geometry).

6. **`ingest --entities` would lose data on multi-file fetches** — the CLI
   calls `store.write_entities` once per fetched path, and entities writes
   are whole-file-replace per source. All three current connectors return
   a single path, so this is latent — but the interface returns
   `list[Path]` and production fetches already return several files.
   Concatenate parsed entity frames per source before writing.

## Operability

7. **A failed multi-file ingest reports nothing useful** — if file 3 of 36
   fails, partitions written by files 1–2 stay written but are never
   printed, and the error doesn't name the failing file. Wrap the per-file
   loop to add the path to the error and print what was written before the
   failure. (Upserts are idempotent, so re-running heals the store.)

## Efficiency

8. **Per-file writes rewrite the same partition dozens of times** — a
   full-year ANP ingest calls `write_production` ~36 times against the
   same `(source, year)` partition, each call re-reading and rewriting the
   whole file. Concatenating the parsed frames per run and writing once is
   semantics-identical (`drop_duplicates(keep="last")` on the concat
   already resolves the pré-sal/mar overlap in fetch order — verified) and
   cuts partition I/O by roughly an order of magnitude on backfills.

## Robustness / reuse

9. **`anp._read_entities` has no cp1252 fallback** — its sibling `_read`
   retries with cp1252 because some ANP vintages are cp1252-encoded; the
   cadastre reader just crashes. Share `_read`'s fallback.

10. **`argentina.fetch_entities` duplicates `fetch`'s CKAN flow** — ~18
    near-identical lines (package_show → CSV-resource filter → download);
    only the filter predicate and error message differ. Extract a shared
    resolve-CKAN-resource helper so a portal change is fixed once.

## Smaller items (from per-task reviews)

- `store.py`: `_UPSERT_KEY`/`_SORT_KEY` are identical constants — merge.
- `tests/test_registry.py`: `test_source_without_entities_raises_pointed_error`
  now always skips (all connectors implement entities); rewrite it against
  an ad-hoc `Source` subclass like the neighboring fetch test.
- `validate_entities` has no coordinate-bounds check (|lon| ≤ 180,
  |lat| ≤ 90) — a lon/lat swap or mislabeled projection validates fine.
- `schema.py` duplicate-id error truncates its sample before sorting
  (`unique()[:5]` then sort) — sort first.
- `store.sql` on an empty store exposes `entities.geometry` with a
  different type (object) than a populated store (WKB blob).
- Ingest's `--month` help text on the `ingest` subparser still reads like
  the `fetch` one.

## Empirically refuted (checked against live data, no action needed)

- ANP well names duplicating in the cadastre: the live 31,273-row WFS
  export has zero duplicate `POCO` values.
- Pré-sal/mar write order mattering for stored values: overlapping rows in
  three sampled months are byte-identical, so upsert order is not
  load-bearing (it only mattered for the old concat-and-sum approach).
