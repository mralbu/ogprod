"""Persistent local store: Parquet/GeoParquet files queried through DuckDB.

The store is a directory of plain files — Parquet for production
(hive-partitioned by source and year), GeoParquet for entities (one file per
source). DuckDB is only the query engine (see ``load_production`` /
``sql``), never the storage format, so everything here is readable directly
with pandas/geopandas and servable from static hosting.

Publishers restate history, so production writes upsert on
``(source, entity_id, period, product)`` by rewriting the affected
``(source, year)`` partitions — never by appending.
"""

from __future__ import annotations

import os
from pathlib import Path

import geopandas as gpd
import pandas as pd

from ogprod import schema

#: Default store location, overridable via ``OGPROD_STORE`` or per call.
DEFAULT_ROOT = Path("data/store")
_ENV_VAR = "OGPROD_STORE"

_UPSERT_KEY = ["entity_id", "period", "product"]
_SORT_KEY = ["entity_id", "period", "product"]


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
    ``(source, entity_id, period, product)``; duplicate keys within ``frame``
    itself keep the last occurrence (matters for ANP, whose pré-sal file
    restates a subset of the mar file).
    """
    schema.validate(frame)
    frame = frame.drop_duplicates(subset=["source", *_UPSERT_KEY], keep="last")
    written = []
    base = resolve_root(root)
    frame = frame.copy()
    frame["year"] = frame["period"].dt.year
    for (source, year), group in frame.groupby(["source", "year"]):  # type: ignore
        path = _partition_path(base, str(source), int(year))
        merged = group
        if path.exists():
            existing = pd.read_parquet(path)
            incoming_keys = pd.MultiIndex.from_frame(group[_UPSERT_KEY])
            kept = existing[~pd.MultiIndex.from_frame(existing[_UPSERT_KEY]).isin(incoming_keys)]
            merged = pd.concat([kept, group], ignore_index=True)
        merged = merged.sort_values(_SORT_KEY, ignore_index=True)[list(schema.CANONICAL_COLUMNS)]
        _atomic_write_parquet(merged, path)
        written.append(path)
    return written


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
