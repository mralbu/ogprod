"""Persistent local store: Parquet/GeoParquet files queried through DuckDB.

:class:`Store` is the interface: ``ingest`` fetches, parses and upserts a
source; ``production``/``entities``/``sql``/``coverage`` read it back. The
module-level functions below are its internal pieces.

The store is a directory of plain files — Parquet for production
(hive-partitioned by source and year), GeoParquet for entities (one file per
source). DuckDB is only the query engine (see ``load_production`` /
``sql``), never the storage format, so everything here is readable directly
with pandas/geopandas and servable from static hosting.

Publishers restate history, so production writes upsert on
``(source, entity_id, period, product, field)`` by rewriting the affected
``(source, year)`` partitions — never by appending.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar, cast

import duckdb
import geopandas as gpd
import httpx
import pandas as pd

from ogprod import schema, sources
from ogprod.sources import base

_ENV_VAR = "OGPROD_STORE"
_Frame = TypeVar("_Frame", bound=pd.DataFrame)

#: Flush accumulated parsed rows to the store once a batch reaches this
#: size — bounds memory on whole-archive ingests while rewriting each
#: (source, year) partition far less than once per raw file.
_BATCH_ROWS = 2_000_000

#: Upsert key within a (source, year) partition — ``schema.PRODUCTION_KEY``
#: minus the partition's source; rows are also kept sorted by it.
_KEY = [column for column in schema.PRODUCTION_KEY if column != "source"]


def _key_index(frame: pd.DataFrame) -> pd.MultiIndex:
    """The upsert-key index of ``frame``; null fields compare equal."""
    key = frame[_KEY].copy()
    key["field"] = key["field"].fillna("")
    return pd.MultiIndex.from_frame(key)


def resolve_root(root: str | Path | None = None) -> Path:
    """The store root: explicit argument, else $OGPROD_STORE, else the user data dir.

    The user data dir is ``$XDG_DATA_HOME/ogprod/store``, falling back to
    ``~/.local/share/ogprod/store`` — on macOS too, so the path is short and
    predictable for opening the files with other tools.
    """
    if root is not None:
        return Path(root).expanduser()
    if env := os.environ.get(_ENV_VAR):
        return Path(env).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "ogprod" / "store"


def _partition_path(root: Path, source: str, year: int) -> Path:
    return root / "production" / f"source={source}" / f"year={year}" / "data.parquet"


def _atomic_write_parquet(frame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def write_production(frame: pd.DataFrame, root: str | Path | None = None) -> list[Path]:
    """Upsert a canonical frame into the store; returns rewritten partition paths.

    Incoming rows replace stored rows with the same
    ``(source, entity_id, period, product, field)``. ``frame`` must not repeat
    a key (``schema.validate`` rejects it); combining overlapping files is
    the caller's job — see ``combine_restatements``.
    """
    schema.validate(frame)
    written = []
    base = resolve_root(root)
    frame = frame.copy()
    frame["year"] = frame["period"].dt.year
    for (source, year), group in frame.groupby(["source", "year"]):  # type: ignore
        path = _partition_path(base, str(source), int(year))
        merged = group
        if path.exists():
            existing = pd.read_parquet(path)
            kept = existing[~_key_index(existing).isin(_key_index(group))]
            merged = pd.concat([kept, group], ignore_index=True)
        merged = merged.sort_values(_KEY, ignore_index=True)[list(schema.CANONICAL_COLUMNS)]
        _atomic_write_parquet(merged, path)
        written.append(path)
    return written


def combine_restatements(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Concatenate parsed frames in order; a later frame's row replaces an
    earlier one with the same ``schema.PRODUCTION_KEY``.

    For publishers whose files overlap (ANP's pré-sal file restates a subset
    of the mar file). Each frame must be valid on its own, so a key repeated
    *within* one file still fails loudly instead of losing volume.
    """
    frames = [schema.validate(frame) for frame in frames]
    combined = pd.concat(frames, ignore_index=True) if frames else schema.empty_frame()
    return combined.drop_duplicates(subset=list(schema.PRODUCTION_KEY), keep="last")


def _entities_file(root: Path, source: str) -> Path:
    return root / "entities" / f"{source}.parquet"


def write_entities(entities: gpd.GeoDataFrame, root: str | Path | None = None) -> list[Path]:
    """Replace each source's entities file (GeoParquet); returns written paths.

    Entities are small (10³–10⁴ rows per source), so whole-file replacement
    per source beats merge logic.
    """
    schema.validate_entities(entities)
    base = resolve_root(root)
    written = []
    for source, group in entities.groupby("source"):
        path = _entities_file(base, str(source))
        group = group.reset_index(drop=True)[list(schema.ENTITY_COLUMNS)]
        _atomic_write_parquet(group, path)
        written.append(path)
    return written


def load_entities(
    *,
    source: str | None = None,
    entity_type: str | None = None,
    root: str | Path | None = None,
) -> gpd.GeoDataFrame:
    """Load entities from the store, optionally filtered by source/entity_type."""
    base = resolve_root(root) / "entities"
    if source is not None:
        files = [base / f"{source}.parquet"]
    elif base.exists():
        files = sorted(base.glob("*.parquet"))
    else:
        files = []
    files = [f for f in files if f.exists()]
    if not files:
        return schema.empty_entities()
    parts = [gpd.read_parquet(f) for f in files]
    entities = parts[0] if len(parts) == 1 else pd.concat(parts, ignore_index=True)
    if entity_type is not None:
        entities = entities[entities["entity_type"] == entity_type].reset_index(drop=True)
    return entities


def _production_files(
    root: Path, source: str | None, start: pd.Timestamp | None, end: pd.Timestamp | None
) -> list[Path]:
    """Partition files matching the source/year pruning implied by the filters."""
    base = root / "production"
    if not base.exists():
        return []
    source_dirs = [base / f"source={source}"] if source else sorted(base.glob("source=*"))
    files = []
    for source_dir in source_dirs:
        for year_dir in sorted(source_dir.glob("year=*")):
            year = int(year_dir.name.split("=", 1)[1])
            if start is not None and year < start.year:
                continue
            if end is not None and year > end.year:
                continue
            path = year_dir / "data.parquet"
            if path.exists():
                files.append(path)
    return files


def _files_literal(files: list[Path]) -> str:
    quoted = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
    return f"[{quoted}]"


def load_production(
    *,
    source: str | Iterable[str] | None = None,
    entity_type: str | Iterable[str] | None = None,
    entity_id: str | Iterable[str] | None = None,
    field: str | Iterable[str] | None = None,
    basin: str | Iterable[str] | None = None,
    region: str | Iterable[str] | None = None,
    operator: str | Iterable[str] | None = None,
    product: str | Iterable[str] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    root: str | Path | None = None,
) -> pd.DataFrame:
    """Load canonical production rows, filtered with DuckDB pushdown.

    String filters accept one value or an iterable; ``start``/``end`` bound
    ``period`` inclusively. Filters combine with AND. Source and year filters
    additionally prune which partition files are read at all.
    """
    start_ts: pd.Timestamp | None = None if start is None else pd.Timestamp(start)  # type: ignore
    end_ts: pd.Timestamp | None = None if end is None else pd.Timestamp(end)  # type: ignore
    single_source = source if isinstance(source, str) else None
    files = _production_files(resolve_root(root), single_source, start_ts, end_ts)
    if not files:
        return schema.empty_frame()

    conditions, params = [], []
    string_filters = {
        "source": source,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "field": field,
        "basin": basin,
        "region": region,
        "operator": operator,
        "product": product,
    }
    for column, value in string_filters.items():
        if value is None:
            continue
        values = [value] if isinstance(value, str) else [str(v) for v in value]
        if not values:
            # An empty filter list matches nothing.
            return schema.empty_frame()
        conditions.append(f"{column} IN ({', '.join('?' for _ in values)})")
        params.extend(values)
    if start_ts is not None:
        conditions.append("period >= ?")
        params.append(start_ts.to_pydatetime())
    if end_ts is not None:
        conditions.append("period <= ?")
        params.append(end_ts.to_pydatetime())

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    query = (
        f"SELECT * FROM read_parquet({_files_literal(files)}){where} "
        "ORDER BY source, entity_id, period, product"
    )
    frame = duckdb.execute(query, params).df()
    frame["period"] = frame["period"].astype("datetime64[ns]")
    # canonical string columns; shared with schema by design
    frame = frame.astype({column: "string" for column in schema._STRING_COLUMNS})
    return frame[list(schema.CANONICAL_COLUMNS)]


def sql(query: str, root: str | Path | None = None) -> pd.DataFrame:
    """Run a DuckDB query over the store.

    Two views are available: ``production`` (canonical columns) and
    ``entities`` (entity columns; ``geometry`` arrives as WKB bytes — use
    :func:`load_entities` when you want real geometries).
    """
    base = resolve_root(root)
    with duckdb.connect() as con:
        production_files = _production_files(base, None, None, None)
        if production_files:
            con.execute(
                "CREATE VIEW production AS SELECT * FROM "
                f"read_parquet({_files_literal(production_files)})"
            )
        else:
            con.register("production", schema.empty_frame())
        entities_dir = base / "entities"
        entity_files = sorted(entities_dir.glob("*.parquet")) if entities_dir.exists() else []
        if entity_files:
            con.execute(
                "CREATE VIEW entities AS SELECT * FROM "
                f"read_parquet({_files_literal(entity_files)}, union_by_name=true)"
            )
        else:
            # Match the populated store's column types (geometry = WKB BLOB) so
            # queries behave identically against an empty store.
            columns = ", ".join(
                f"CAST(NULL AS {'BLOB' if column == 'geometry' else 'VARCHAR'}) AS {column}"
                for column in schema.ENTITY_COLUMNS
            )
            con.execute(f"CREATE VIEW entities AS SELECT {columns} WHERE false")
        return con.execute(query).df()


@dataclass(frozen=True)
class IngestResult:
    """What one :meth:`Store.ingest` call wrote."""

    source: str
    #: Production rows upserted (after resolving restatements across files).
    rows: int
    first_period: pd.Timestamp | None
    last_period: pd.Timestamp | None
    #: Production partition files rewritten.
    partitions: tuple[Path, ...]
    #: Entity rows written, or ``None`` when entities were not (re)ingested.
    entities: int | None

    def __str__(self) -> str:
        span = (
            f" {self.first_period:%Y-%m}..{self.last_period:%Y-%m}"
            if self.first_period is not None
            else ""
        )
        entities = "unchanged" if self.entities is None else f"{self.entities:,}"
        return (
            f"{self.source}: {self.rows:,} production rows{span} "
            f"in {len(self.partitions)} partition(s); entities: {entities}"
        )


class Store:
    """A local production/entities store — the way into ogprod.

    ``Store()`` resolves its root from ``$OGPROD_STORE``, else
    ``$XDG_DATA_HOME/ogprod/store``, else ``~/.local/share/ogprod/store``.
    Single writer: two processes ingesting into one root concurrently race.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = resolve_root(root)

    def __repr__(self) -> str:
        return f"Store({str(self.root)!r})"

    def ingest(
        self,
        source: str,
        *,
        files: Sequence[str | Path] | None = None,
        entities: bool = True,
        refresh: bool = False,
        client: httpx.Client | None = None,
        **options: Any,
    ) -> IngestResult:
        """Fetch, parse and upsert ``source`` into the store.

        Without ``files``, raw files are downloaded into a temporary directory
        (removed afterwards); ``options`` (``year``, ``month``) go to the
        connector's fetch, and an option it doesn't take raises ``TypeError``.
        With ``files``, those already-downloaded files are ingested instead.
        Files are parsed in the connector's restatement order, so a file that
        restates another's rows (ANP's pré-sal over mar) wins rather than
        double-counting.

        Entities (well coordinates / field ring fences) are ingested too
        when the store has none for ``source`` yet, or when ``refresh``;
        connectors whose ``fetch_entities`` raises ``NotImplementedError``
        are skipped, and an empty entity download writes nothing
        (``entities == 0``). Pass ``entities=False`` to skip them (e.g.
        offline with ``files``).

        If anything fails midway, partitions already flushed stay written and
        the error propagates with a note listing them; re-running the same
        ingest heals the store.
        """
        connector = sources.get(source)
        if files is not None and options:
            raise TypeError(
                f"{sorted(options)} select what to fetch; omit them when passing files="
            )
        written: list[Path] = []
        try:
            with tempfile.TemporaryDirectory(prefix="ogprod-") as tmp:
                if files is None:
                    paths = connector.fetch(Path(tmp) / "production", client=client, **options)
                else:
                    paths = [Path(f) for f in files]
                rows, first, last = self._ingest_production(connector, paths, written)
                entity_rows = None
                if entities and (refresh or not _entities_file(self.root, source).exists()):
                    entity_rows = self._ingest_entities(connector, Path(tmp), client)
        except Exception as error:
            if written:
                listing = "\n".join(f"  {path}" for path in dict.fromkeys(written))
                error.add_note(
                    "partitions already written before the failure "
                    f"(re-running the ingest heals the store):\n{listing}"
                )
            raise
        return IngestResult(
            source=source,
            rows=rows,
            first_period=first,
            last_period=last,
            partitions=tuple(dict.fromkeys(written)),
            entities=entity_rows,
        )

    def _ingest_production(
        self, connector: base.Source, paths: list[Path], written: list[Path]
    ) -> tuple[int, pd.Timestamp | None, pd.Timestamp | None]:
        # Parse in restatement order and write in large batches:
        # combine_restatements lets a later file replace an earlier file's
        # rows within a batch, and since an upsert replaces stored rows with
        # incoming ones, writing batches in order keeps that across batches.
        # Each (source, year) partition is rewritten once per batch, not once
        # per raw file. Partitions are appended to the caller's ``written`` as
        # they flush, so a later failure can still report them.
        rows = 0
        first: pd.Timestamp | None = None
        last: pd.Timestamp | None = None
        batch: list[pd.DataFrame] = []
        batch_rows = 0

        def flush() -> None:
            nonlocal rows, first, last, batch, batch_rows
            combined = combine_restatements(batch)
            written.extend(write_production(combined, root=self.root))
            if len(combined):
                rows += len(combined)
                low, high = combined["period"].min(), combined["period"].max()
                first = low if first is None else min(first, low)
                last = high if last is None else max(last, high)
            batch, batch_rows = [], 0

        for path in connector.restatement_order(paths):
            frame = _parse_naming_file(connector.parse, path)
            batch.append(frame)
            batch_rows += len(frame)
            if batch_rows >= _BATCH_ROWS:
                flush()
        if batch:
            flush()
        return rows, first, last

    def _ingest_entities(
        self, connector: base.Source, tmp: Path, client: httpx.Client | None
    ) -> int | None:
        """Entity rows written; ``None`` when the connector has no entity download."""
        try:
            paths = connector.fetch_entities(tmp / "entities", client=client)
        except NotImplementedError:
            return None
        if not paths:
            return 0  # nothing to write; keep whatever entities are stored
        frames = [_parse_naming_file(connector.parse_entities, path) for path in paths]
        merged = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        write_entities(cast(gpd.GeoDataFrame, merged), root=self.root)
        return len(merged)

    def production(
        self,
        *,
        source: str | Iterable[str] | None = None,
        entity_type: str | Iterable[str] | None = None,
        entity_id: str | Iterable[str] | None = None,
        field: str | Iterable[str] | None = None,
        basin: str | Iterable[str] | None = None,
        region: str | Iterable[str] | None = None,
        operator: str | Iterable[str] | None = None,
        product: str | Iterable[str] | None = None,
        start: str | pd.Timestamp | None = None,
        end: str | pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Canonical production rows, one per (entity, month, product, field), in m³.

        String filters accept one value or an iterable and combine with AND;
        ``start``/``end`` bound ``period`` inclusively.
        """
        return load_production(
            source=source,
            entity_type=entity_type,
            entity_id=entity_id,
            field=field,
            basin=basin,
            region=region,
            operator=operator,
            product=product,
            start=start,
            end=end,
            root=self.root,
        )

    def entities(
        self, *, source: str | None = None, entity_type: str | None = None
    ) -> gpd.GeoDataFrame:
        """Entities as a GeoDataFrame in EPSG:4326; joins production on (source, entity_id)."""
        return load_entities(source=source, entity_type=entity_type, root=self.root)

    def sql(self, query: str) -> pd.DataFrame:
        """Run a DuckDB query over the ``production`` and ``entities`` views.

        ``entities.geometry`` arrives as WKB bytes; use :meth:`entities` for
        real geometries.
        """
        return sql(query, root=self.root)

    def coverage(self) -> pd.DataFrame:
        """What the store holds: one row per source.

        Columns: ``source``, ``rows`` (production rows), ``producing_entities``
        (distinct entity ids with production), ``first_period``,
        ``last_period``, ``stored_entities`` (entity rows; null when none).
        """
        frame = self.sql(
            """
            WITH p AS (
                SELECT source, count(*) AS rows,
                       count(DISTINCT entity_id) AS producing_entities,
                       min(period) AS first_period, max(period) AS last_period
                FROM production GROUP BY source
            ), e AS (
                SELECT source, count(*) AS stored_entities FROM entities GROUP BY source
            )
            SELECT coalesce(p.source, e.source) AS source,
                   coalesce(p.rows, 0) AS rows,
                   coalesce(p.producing_entities, 0) AS producing_entities,
                   p.first_period, p.last_period, e.stored_entities
            FROM p FULL OUTER JOIN e ON p.source = e.source
            ORDER BY 1
            """
        )
        return frame.astype(
            {
                "source": "string",
                "rows": "int64",
                "producing_entities": "int64",
                "first_period": "datetime64[ns]",
                "last_period": "datetime64[ns]",
                "stored_entities": "Int64",
            }
        )


def _parse_naming_file(parse: Callable[[Path], _Frame], path: Path) -> _Frame:
    """``parse(path)``, with the failing file's name added to a ValueError."""
    try:
        return parse(path)
    except ValueError as error:
        raise ValueError(f"{error} (while parsing {path})") from error
