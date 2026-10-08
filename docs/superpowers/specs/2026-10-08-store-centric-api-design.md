# Store-centric Python API — design

Status: implemented 2026-10-08 (`784b16c`); review findings in
`docs/follow-ups.md`.

## Goal

Make the Python import surface small and hard to misuse. Every workflow goes
through one deep module, `ogprod.Store`: data is ingested into the store and
read back from it. The stage that turns raw files into a store (fetch, parse
in restatement order, combine, upsert), which today lives only in the CLI,
moves behind that interface, so Python callers and the CLI share one
implementation.

## Problems with the current surface

1. **Ingest orchestration is CLI-only.** The steps fetch → parse in order →
   `store.combine_restatements` → batched upsert live in
   `__main__._ingest`. In Python, the natural thing to write is
   `pd.concat([ogprod.load("anp", p) for p in paths])`, which silently
   double-counts ANP offshore oil, because the pré-sal file restates a subset
   of the mar file.
2. **Restatement order is implicit.** Keep-last is only correct if the
   pré-sal file comes after the mar file. Today that holds only because
   `sorted()` happens to put `..._mar.csv` before `..._presal.csv`. The rule
   is ANP knowledge, but it sits in generic code.
3. **The same verb means two things.** `ogprod.load_entities(name, file)`
   parses a raw file, while `store.load_entities(source=...)` reads the store.
   `ogprod.load` vs `store.load_production` has the same split.
4. **Pass-through wrappers.** `ogprod.load/fetch/load_entities/fetch_entities`
   are each just `get(name).<method>(...)`, so deleting them would remove no
   complexity. `to_m3(v, u)` is the same as `convert(v, u)`.
5. **Connector-author plumbing at the top level.** `validate`,
   `validate_entities`, `CANONICAL_COLUMNS`, `ENTITY_COLUMNS`,
   `CANONICAL_UNIT`, `canonical_id` and `get` are for people writing
   connectors, but they crowd analysts' autocomplete.
6. **`**kwargs: Any` on connector `fetch` swallows typos.**
   `fetch("anp", d, yaer=2023)` ignores the typo, so `year` is `None` and the
   whole 2005–2023 archive is downloaded.
7. **Every notebook threads `root=STORE` through every call** and writes its
   own "skip the download if the file exists" logic.

## Interface

```python
import ogprod

db = ogprod.Store()                         # or Store(root)
db.ingest("anp", year=2023, month=6)        # production + entities
db.ingest("anp", files=my_paths)            # files downloaded by other means
db.production(source="anp", product="oil", start="2023-01")
db.entities(source="anp")                   # GeoDataFrame
db.sql("select ...")                        # views: production, entities
db.coverage()                               # what's in the store

ogprod.available()                          # ['anp', 'argentina', 'sodir']
ogprod.convert(1.0, "m3", "bbl")
ogprod.Product, ogprod.SchemaError
```

The top level shrinks from 17 names plus a second import (`ogprod.store`)
to 5: `Store`, `available`, `convert`, `Product`, `SchemaError`.

`available()` keeps its name: renaming it to `sources()` would collide with
the `ogprod.sources` subpackage.

### `Store(root=None)`

The store root is resolved in this order:

1. the `root` argument;
2. `$OGPROD_STORE`;
3. `$XDG_DATA_HOME/ogprod/store`;
4. `~/.local/share/ogprod/store`.

Steps 3–4 apply on macOS too (not `~/Library/Application Support`): users
open these files directly with DuckDB, pandas and QGIS, so a short path they
can predict matters more than following the platform convention. No
`platformdirs` dependency. `repr(Store())` shows the resolved path.

It's called a *store*, not a cache. The contents can't reliably be rebuilt:
publishers stop publishing data (ANP bulk files end at 2023, and pre-2005
history depends on the Wayback Machine), and endpoints move. A store also
holds the sources and years the user chose to ingest. So it doesn't belong
in `~/.cache` (disposable by convention) or `~/.config` (settings).

Migration: an existing `data/store/` keeps working with
`OGPROD_STORE=data/store` or `Store("data/store")`.

### `Store.ingest(source, *, files=None, entities=True, refresh=False, client=None, **options) -> IngestResult`

- **Production:** without `files`, raw files are fetched into a temporary
  directory, which is deleted afterwards. With `files`, those paths are
  parsed and nothing is fetched for production. `**options` (`year`,
  `month`) is passed to the connector's `fetch`, which now declares its
  options explicitly with no `**kwargs`, so an unknown option raises
  `TypeError`. Passing `options` together with `files` is a `TypeError`.
- **Restatement order:** the paths go through
  `Source.restatement_order(paths)` before parsing; see Connector changes.
  This applies to user-supplied `files` too, so callers can't get the order
  wrong.
- **Batching:** the batched upsert moves here unchanged from the CLI
  (flushing every `_BATCH_ROWS` rows). Writing batches in order keeps the
  keep-last semantics across batch boundaries, because an upsert replaces
  stored rows with the incoming ones.
- **Entities** (default on): ingested when the store has no entities for
  `source`, or when `refresh=True`. If entities for the source are already
  stored, the download is skipped. A connector with no entity support
  (the base raises `NotImplementedError`) is skipped without error. Offline
  users with `files` pass `entities=False`.
- **Failure mid-run:** partitions already flushed stay written. The store is
  upsert-idempotent, so re-running heals it, as today. The exception
  propagates. `IngestResult` is only returned on success.
- **`client`:** injected into both fetches; tests use `MockTransport`.

`IngestResult` is a frozen dataclass:

- `source`;
- `rows` (production rows upserted);
- `first_period` and `last_period`;
- `partitions` (paths rewritten);
- `entities` (rows written, or `None` if skipped).

It has a one-line `__str__` for the CLI and notebooks. `ingest` doesn't
return the frame: that would bring back a second way to get data. Read it
with `production()`.

### `Store.production(...)`, `Store.entities(...)`, `Store.sql(query)`

These are today's `store.load_production`, `store.load_entities` and
`store.sql`, with unchanged filters and semantics, bound to the store's root.

### `Store.coverage() -> pd.DataFrame`

One row per source:

- `source`;
- `rows`;
- `producing_entities` (distinct `entity_id`s in production);
- `first_period` and `last_period`;
- `stored_entities` (entity rows; null if no entities are stored).

It's computed from the files through `sql`, with no manifest. It answers
"what's in my store?", which matters now that the store is long-lived state.

## Connector changes (`ogprod.sources`)

- **`Source.restatement_order(paths) -> list[Path]`.** The default returns
  `paths` unchanged. ANP overrides it to make its rule explicit: terra, then
  mar, then pré-sal last, so the pré-sal file restates the mar file.
  It's a stable sort on the environment named in the (accent-stripped,
  lowercased) file name, so files keep their given order within each group.
  Files that name no environment, such as CDP exports, go first. On a
  whole-archive ingest this groups files by environment rather than by
  period, so a year partition may be rewritten once per environment batch.
  That only costs speed: correctness doesn't depend on period order.
  `store.combine_restatements` stays as the keep-last implementation.
- **`fetch` / `fetch_entities` signatures** list their real keyword options
  and drop `**kwargs: Any`, in the base class as well. An override that adds
  optional keywords is compatible with the base; one that drops the base's
  `**kwargs` is not.
- **`@base.register` is generic** (`TypeVar` bound to `Source`). Typed as
  `type[Source] -> type[Source]`, it erased each connector's class, so
  `anp.ANP().fetch(year=...)` didn't type-check. `sources.get(name)` still
  returns the base `Source`, so statically typed callers that need
  per-source options use the connector class directly. Each connector's options:
  - `anp.fetch`: `year`, `month`;
  - `argentina.fetch`: `year` (required);
  - `sodir.fetch`: none.
- **`ogprod.sources` stays the advanced layer:** `get`, `available`,
  `Source`, `register`, `build_frame`, `build_entities`. Use it to parse a
  single file for diagnostics, like notebook 02's terra/mar/pré-sal
  comparison (`ogprod.sources.get("anp").parse(path)`), or to download raw
  files without ingesting them. The module-level `load`, `fetch`,
  `load_entities` and `fetch_entities` wrappers are removed.
- **`ogprod.schema` and `ogprod.units` are unchanged.** They're imported
  explicitly by whoever needs them.

## Store module internals (`ogprod.store`)

`write_production`, `write_entities`, `combine_restatements`,
`load_production`, `load_entities`, `sql` and `resolve_root` remain as
module functions. They're the `Store` class's internal pieces, and the
existing `test_store.py` tests them directly. They're not re-exported from
`ogprod`. `DEFAULT_ROOT` and `resolve_root` change to the new resolution
order above.

## CLI

The CLI becomes a thin adapter over `Store`:

- `python -m ogprod ingest <source> [--year --month] [--files PATH ...] [--no-entities] [--refresh] [--store PATH]`
  prints the `IngestResult`. The `--entities` flag is removed, since entities
  are on by default and `--no-entities` turns them off.
- `python -m ogprod coverage [--store PATH]` prints the coverage table.
- `python -m ogprod fetch <source> [--year --month] [--entities] [--dest]`
  stays as the raw-download tool. Its default `--dest` stays `data/`.
- `just ingest` / `just fetch` keep working, with their recipe descriptions
  updated.

## Breaking changes (default: remove, no aliases)

The package is at 0.1.0, and keeping old and new APIs side by side is
exactly the surface this spec removes. These names are removed with no
deprecation aliases:

- top-level `load`, `fetch`, `load_entities`, `fetch_entities`, `get`,
  `validate`, `validate_entities`, `canonical_id`, `to_m3`,
  `CANONICAL_COLUMNS`, `ENTITY_COLUMNS`, `CANONICAL_UNIT`, `EntityType`;
- the `ogprod.sources` module-level wrappers.

The default store location changes. `__version__` goes to 0.2.0.

## Tests

- New `tests/test_ingest.py` goes through the interface:
  - `Store(tmp_path).ingest(..., client=MockTransport)` for each connector,
    then `production()` and `entities()`;
  - ANP terra/mar/pré-sal fixtures passed in shuffled order with `files=`
    are not double-counted;
  - entities are skipped when already present, and refetched with
    `refresh=True`;
  - a connector with no entity support is skipped;
  - an unknown option raises `TypeError`;
  - passing `options` with `files` raises `TypeError`;
  - a mid-run parse failure leaves flushed partitions and raises;
  - `coverage()` on an empty store and on a populated one.
- `test_cli.py` shrinks to checking that arguments map to `Store` calls, and
  covers the `coverage` command.
- `test_registry.py` drops the wrapper tests; `test_store.py` covers the new
  root resolution (XDG and home fallbacks).
- `restatement_order` is covered in `test_ingest.py` (with the shuffled-order
  ingest test it exists for) rather than `test_sources_anp.py`.
- `test_live_smoke.py` keeps the same checks, rewritten from the removed
  wrappers to `sources.get(...)` / connector classes.

## Docs and examples

- **Update:**
  - README quickstart: `Store()` / `ingest` / `production`;
  - `docs/design.md`: the "connector → canonical frame → store → loaders/CLI"
    chain becomes "connector → Store.ingest → Store queries", plus the new
    root resolution;
  - `docs/downloading.md`: `ingest(files=...)` for manual downloads;
  - CLAUDE.md: architecture and the store root.
- **Notebooks** drop `STORE`, `root=` and the hand-written skip-if-downloaded
  logic, and use `db = ogprod.Store()` and `db.ingest(...)`. Notebook 02 is
  the exception: its per-file terra/mar/pré-sal comparison needs the raw CSVs,
  so it keeps them in `data/anp/` (reused when present), parses them through
  the ANP connector, and ingests them with `files=`.

## Out of scope

- **Prebuilt-data mirror:** GitHub Release assets or Hugging Face, with a
  second `ingest` origin (`origin="mirror" | "publisher"`). It gets its own
  spec once `Store.ingest` exists. Measurements on the current store:
  - switching snappy → zstd saves 20–25% losslessly;
  - float32 saves ≤20% and breaks exact cross-checks of totals;
  - float16 overflows (4% of ANP volumes, 32% of Sodir).
  So the mirror stays float64 + zstd.
  A live Argentina 2026 ingest (Jan–Sep, ~2M rows) takes 5.5 MB at 2.8 B/row,
  so the full ~20-year history is about 150 MB. The whole mirror stays well
  under 1 GB.
- **Keeping raw downloads between runs** (`~/.cache/ogprod/raw`). Callers
  who need it fetch through `ogprod.sources` and ingest with `files=`.
