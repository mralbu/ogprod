"""Persistent local store: Parquet/GeoParquet files queried through DuckDB.

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
from collections.abc import Iterable
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd

from ogprod import schema

#: Default store location, overridable via ``OGPROD_STORE`` or per call.
DEFAULT_ROOT = Path("data/store")
_ENV_VAR = "OGPROD_STORE"

#: Upsert key within a (source, year) partition — ``schema.PRODUCTION_KEY``
#: minus the partition's source; rows are also kept sorted by it.
_KEY = [column for column in schema.PRODUCTION_KEY if column != "source"]


def _key_index(frame: pd.DataFrame) -> pd.MultiIndex:
    """The upsert-key index of ``frame``; null fields compare equal."""
    key = frame[_KEY].copy()
    key["field"] = key["field"].fillna("")
    return pd.MultiIndex.from_frame(key)


def resolve_root(root: str | Path | None = None) -> Path:
    """The store root: explicit argument, else $OGPROD_STORE, else data/store."""
    if root is not None:
        return Path(root)
    return Path(os.environ.get(_ENV_VAR, DEFAULT_ROOT))


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
