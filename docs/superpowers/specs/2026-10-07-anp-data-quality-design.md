# ANP data quality — in-file duplicates and the 2003–2005 gas gap — design

Two defects in ANP's published well-level data affect both the current bulk
zips (2005–2023) and the archived 1941–2004 files
(see [historical spec](2026-10-07-anp-historical-wayback-design.md)). This
spec decides how ogprod handles them. The goal is to fix what can be fixed
safely, flag what can't, and never invent volumes.

Everything below was measured on 2026-10-07 against all 357 CSVs in the
current ANP zips (fetched with `ogprod.fetch("anp", …)`) and the 50 archived
pre-2005 files.

## Defect 1 — the same well-month repeated within a file

### Current zips (2005–2023)

Today `anp.parse` raises `SchemaError` on **3 of 357** files. That breaks
`just ingest anp` for those periods. The cause is the in-file duplicate-key
check added in 0cd58c8:

| File | Rows | Repeated keys | What differs between the copies |
|---|---|---|---|
| `2005/2005_producao_terra.csv` | 110,217 | 5,472 | 2,624: royalty-gas column only · 2,213: associated gas present in one copy (with 0 h production time), 0 in the other (with full hours) · 525: production hours only · 98: nothing (exact copies) · 12: non-associated gas present in one copy only |
| `2019/2019_05_producao_mar.csv` | 1,516 | 26 | Crude-quality columns only (`Corrente`, `Grau API`, distillate fractions): one row per crude stream, same volumes |
| `2019/2019_05_producao_presal.csv` | — | 3 | Same as 2019-05 mar |

Across all 5,501 groups and every volume column, **no group has two
different non-zero values**. Within a group, each product's volume is either
repeated as-is or present in one row and zero in the others.

What the 2005 copies are: comparing with the archived snapshot of 2005 (taken
in 2018) shows the second copy of a well-month is the leftover of the
state cross-join described below. The archive files it under the wrong state
(e.g. `7-FBE-8-BA` under Espírito Santo, carrying the gas). The current zip
moved it back to Bahia but never merged it. Oil appears in both copies. Gas,
and the production hours, appear in only one copy each. So the well produced
the oil **once**, together with that gas.
Summing the copies (which the archived file's totals effectively do) counts
2005 onshore oil twice for those wells: 12.020 vs 11.841 Mm³, +1.5%.

### Archived files (1941–2004)

101,400 repeated groups, from the state cross-join. Some field names exist in
two states (FAZENDA BOA ESPERANÇA in BA/ES, FAZENDA BELÉM in BA/CE), and every
well of such a field was listed under both states. In every group exactly one
row's `Estado` matches the state code at the end of the well name. Before
mid-2003 the copies carry identical volumes. In 2003–2004 the volumes split
between the two copies, as in 2005. 586 (group, column) pairs, all in
`producao-terra-2003-{1,2}sem.csv`, disagree only by rounding to 2 decimals
(e.g. gas 0.0745 vs 0.07 e3m3). Another 717 groups in
`producao-mar-2001-2004.csv` split a well-month across `Instalação = Não Informado`
(empty volumes) and the real platform.

### Rule: merge, don't sum and don't drop

The same rule applies to both formats. It lives in a single
`_merge_repeated_rows(frame, key, volume_columns, prefer=None)` helper in
`sources/anp.py` and runs before `build_frame`:

1. Group rows by `(well, period, field)`, the production key without product.
2. Sort each group's rows by preference. For the archived format, rows whose
   `Estado` matches the well's state code come first. Otherwise keep file
   order.
3. For each volume column: the merged value is the **first non-zero value in
   preference order**, or 0 or null if there is none. Metadata (state, basin,
   operator, installation) comes from the first preferred row.
4. **Conflict check.** If two rows have different non-zero values for the same
   column:
   - If their relative difference is ≤ 5e-3, or their absolute difference is
     within the coarser value's rounding (≤ 0.01 in the file's unit), take
     the preferred row's value. This is the 2003 rounding case.
   - Anything else raises `SchemaError` naming the file, the key and the
     values. A future publisher change then fails loudly instead of being
     merged blindly.
5. Exception: rows that split by `Instalação` within an archived file are
   **summed** before steps 1–4, because they are different flows. This
   applies only where one side is `Não Informado` with empty volumes, so the
   sum is effectively a coalesce.

The project rule that "a key repeated within one parsed frame is a
`SchemaError`, never silently deduped" stays in force. These merges are
explicit, documented and specific to each format, they check their own
assumptions, and `build_frame`/`validate` still reject anything left over.
docs/design.md's per-connector notes record the rule, and docs/follow-ups.md
records these counts as of 2026-10-07. ogprod has no logging, so no per-file
log line is emitted.

Effect: 2005 onshore oil drops from 12.020 to 11.841 Mm³. 1941–2003 totals
are unchanged against keeping the state-matched row. 2004 gas recovers
9 e6m3 that keeping only the state-matched row would have lost.

## Defect 2 — gas missing for 2003-07 … 2005-06

In both the archived files and the current 2005 zip, the number of wells
reporting gas collapses for 24 months:

| Period | Wells with gas > 0 | Gas (e6m3/month) |
|---|---|---|
| 2002-07 … 2003-06 | ~8,200–8,400 | ~1,200–1,320 |
| **2003-07 … 2005-06** | **~420–650** | **~8–55** |
| 2005-07 onward | ~8,000–8,500 | ~1,450–1,530 |

Annual totals: 2002 15.5 bcm, then 7.8 (2003), 2.4 (2004) and 9.0 (2005),
recovering to 17.7 bcm in 2006. National gas output was about 16–18 bcm a
year throughout, so this is missing data at the publisher. Oil and water
show no gap. ANP's current 2005 zip has the same hole, so a re-download can't
fill it.

### Rule: flag, never impute

- **No backfill in the production table.** ogprod's job is to normalize what
  regulators publish. Filling in well volumes would invent per-well data.
  The months stay as published: gas rows exist for the ~600 reporting
  wells, and the rest are absent.
- A machine-readable list of known gaps: `anp.KNOWN_GAPS`, a tuple of
  `Gap(product, start, end, description, evidence)`, re-exported for users.
  Notebooks and docs point at it. `parse` emits a single `UserWarning` when a
  file's periods overlap a gap for a product it reports, e.g. "ANP well-level
  gas is incomplete for 2003-07..2005-06 (≈5% of normal volume); see
  ogprod.sources.anp.KNOWN_GAPS". Warnings are easy to filter and don't
  change any data.
- docs/downloading.md (ANP caveats) and docs/design.md get a short "Known
  publisher gaps" note. docs/follow-ups.md tracks it as a watch item: if ANP
  republishes 2003–2005, the warning will flag the change, so drop the gap
  entry then.
- Possible later work, out of scope here: ANP's field-level or state-level
  monthly series (anuário / "produção nacional" datasets) could provide a
  gas total for these months at field grain, in a separate table. That
  table must first be shown to cover 2003–2005. Unverified.

## Not in scope

- Restating the current `anp` 2005 file's `region`/`basin` when they differ
  from the archived copy. The merge keeps the current zip's values.
- The ~0.5% of wells renamed between the archived and the current naming
  (tracked in the historical spec).

## Tests

- Fixtures (real rows, a few each):
  - `anp_2005_repeated.csv`: one exact copy, one royalty-only difference, and
    one oil-in-both/gas-in-one pair.
  - `anp_2019_05_crude_streams.csv`: two crude-stream rows of one well.
  - Archived-format pairs (state cross-join with identical volumes, the 2003
    rounding case, and an installation split), shared with the historical
    spec's fixtures.
- Merge results: oil counted once, gas kept, metadata from the preferred row,
  the 2003 rounding pair resolves to the state-matched value, and a synthetic
  real conflict (oil 100 vs 200) raises `SchemaError`.
- A `KNOWN_GAPS` warning fires for a 2004 gas file, and not for a 2007 file or
  an oil-only frame.
- Regression: `ogprod.load("anp", …)` succeeds on all three fixture shapes.
  The live smoke test needs no change.

## Verification before merging

Re-run the full 357-file parse and the 50 archived files and record in
the PR:
- zero failures;
- the merged-group counts per file;
- 2005 terra oil of 11.841 Mm³ and gas unchanged;
- national oil per year compared against ANP-published annual totals for
  2000, 2005, 2010 and 2020. This follows the "Format caveat" practice in
  docs/design.md.
