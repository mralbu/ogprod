"""Compact Parquet + GeoParquet 1.1 store with DuckDB queries and manifest tracking.

The store keeps only canonical, ZSTD-compressed Parquet files on disk (raw
downloads live in a temporary directory and are discarded after ingestion
unless ``keep_raw=True`` is requested). Production partitions upsert atomically
on :data:`ogprod.schema.PRODUCTION_KEY` so publisher restatements overwrite
prior months without duplicating volume.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from ogprod.audit import AuditReport

import duckdb
import geopandas as gpd
import httpx2
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from duckdb import ColumnExpression, ConstantExpression

from ogprod import geo, schema, sources
from ogprod import mirror as mirror_mod
from ogprod.sources import base

_ENV_VAR = "OGPROD_DATA"
_MANIFEST_VERSION = 2
_BATCH_ROWS = 1_500_000
_PARQUET_ROW_GROUP_SIZE = 128_000


def resolve_root(root: str | Path | None = None) -> Path:
    """Resolve the ogprod data root: explicit arg, ``$OGPROD_DATA``, XDG data dir."""
    if root is not None:
        return Path(root).expanduser()
    if env := os.environ.get(_ENV_VAR):
        return Path(env).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(data_home) / "ogprod"


def store_dir(root: str | Path | None = None) -> Path:
    """Path to the canonical Layer-1 Parquet/GeoParquet store directory."""
    return resolve_root(root) / "store"


def _manifest_path(root: Path) -> Path:
    return store_dir(root) / "manifest.json"


def _partition_path(root: Path, source: str, year: int) -> Path:
    return store_dir(root) / "production" / f"source={source}" / f"year={year}" / "data.parquet"


def _geo_path(root: Path, kind: schema.GeoKind, source: str) -> Path:
    return store_dir(root) / "geo" / kind.value / f"{source}.parquet"


def load_manifest(root: str | Path | None = None) -> dict[str, Any]:
    """Load ``store/manifest.json`` (or an empty template if absent)."""
    path = _manifest_path(resolve_root(root))
    if not path.exists():
        return {
            "schema_version": _MANIFEST_VERSION,
            "units": dict(schema.RATE_UNITS),
            "sources": {},
        }
    return json.loads(path.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any], root: str | Path | None = None) -> Path:
    """Atomically write ``store/manifest.json``."""
    path = _manifest_path(resolve_root(root))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _atomic_write_parquet(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    pq.write_table(
        table,
        tmp,
        row_group_size=_PARQUET_ROW_GROUP_SIZE,
        compression="zstd",
        compression_level=6,
        use_dictionary=True,
        write_statistics=True,
    )
    tmp.replace(path)


def _merge_partition(existing: pa.Table, incoming: pa.Table) -> pa.Table:
    """Upsert ``incoming`` rows into ``existing`` on ``PRODUCTION_KEY`` (keep incoming)."""
    key_cols = list(schema.PRODUCTION_KEY)
    incoming_keys = incoming.select(key_cols).to_pandas()
    existing_keys = existing.select(key_cols).to_pandas()

    inc_idx = pd.MultiIndex.from_frame(incoming_keys)
    ext_idx = pd.MultiIndex.from_frame(existing_keys)
    keep_mask = ~ext_idx.isin(inc_idx)

    if keep_mask.all():
        merged = pa.concat_tables([existing, incoming])
    elif not keep_mask.any():
        merged = incoming
    else:
        kept = existing.filter(pa.array(keep_mask))
        merged = pa.concat_tables([kept, incoming])
    return _sort_production(merged)


def _sort_production(table: pa.Table) -> pa.Table:
    order = [
        ("entity_type", "ascending"),
        ("entity_id", "ascending"),
        ("field_id", "ascending"),
        ("facility_id", "ascending"),
        ("period", "ascending"),
    ]
    return table.sort_by(order)


def _write_production_parts(
    table: pa.Table,
    root: str | Path | None = None,
    *,
    replace: bool = False,
    replace_years: set[int] | None = None,
    already_replaced: set[Path] | None = None,
) -> list[Path]:
    validated = schema.validate_production(table)
    if validated.num_rows == 0:
        return []
    base_root = resolve_root(root)
    row_keys = [
        (src, period.year)
        for src, period in zip(
            validated.column("source").to_pylist(),
            validated.column("period").to_pylist(),
            strict=True,
        )
    ]
    indices_by_part: dict[tuple[str, int], list[int]] = {}
    for idx, pair in enumerate(row_keys):
        indices_by_part.setdefault(pair, []).append(idx)

    written: list[Path] = []
    for (src, year), indices in sorted(indices_by_part.items()):
        part = validated.take(indices)
        path = _partition_path(base_root, src, year)
        should_replace = replace or (
            already_replaced is not None
            and path not in already_replaced
            and (replace_years is None or year in replace_years)
        )
        merged = (
            _sort_production(part)
            if should_replace or not path.exists()
            else _merge_partition(pq.read_table(path), part)
        )
        _atomic_write_parquet(merged, path)
        if should_replace and already_replaced is not None:
            already_replaced.add(path)
        written.append(path)
    return written


def write_production(
    table: pa.Table,
    root: str | Path | None = None,
    *,
    replace: bool = False,
) -> list[Path]:
    """Atomically upsert (or replace) a validated production Arrow table by ``(source, year)``."""
    return _write_production_parts(table, root=root, replace=replace)


def combine_tables(tables: Sequence[pa.Table]) -> pa.Table:
    """Concatenate validated tables, keeping the latest row when keys overlap across files."""
    non_empty = [schema.validate_production(t) for t in tables if t.num_rows > 0]
    if not non_empty:
        return schema.empty_production()
    if len(non_empty) == 1:
        return non_empty[0]
    combined = pa.concat_tables(non_empty)
    key_frame = combined.select(list(schema.PRODUCTION_KEY)).to_pandas()
    keep_mask = ~key_frame.duplicated(keep="last")
    if keep_mask.all():
        return combined
    return combined.filter(pa.array(keep_mask.to_numpy()))


def write_geo(
    gdf: gpd.GeoDataFrame,
    kind: schema.GeoKind,
    root: str | Path | None = None,
    *,
    crs: str | None = None,
) -> list[Path]:
    """Replace each source's GeoParquet 1.1 file for ``kind``."""
    base_root = resolve_root(root)
    written: list[Path] = []
    if len(gdf) == 0:
        return written
    for src, group in gdf.groupby("source", sort=True):
        path = _geo_path(base_root, kind, str(src))
        sub = gpd.GeoDataFrame(group, geometry="geometry", crs=gdf.crs)
        geo.write_geoparquet(sub, path, kind, crs=crs)
        written.append(path)
    return written


def load_geo(
    kind: schema.GeoKind,
    *,
    source: str | None = None,
    root: str | Path | None = None,
) -> gpd.GeoDataFrame:
    """Load one geospatial table (``fields``, ``facilities``, or ``wells``)."""
    folder = store_dir(root) / "geo" / kind.value
    if source is not None:
        files = [folder / f"{source}.parquet"]
    elif folder.exists():
        files = sorted(folder.glob("*.parquet"))
    else:
        files = []
    existing = [f for f in files if f.exists()]
    if not existing:
        return schema.empty_geo(kind)
    frames = [geo.read_geoparquet(f) for f in existing]
    if len(frames) == 1:
        return frames[0]
    combined = pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=schema.GEO_CRS)


def _source_dirs(prod_dir: Path, source: str | Iterable[str] | None) -> list[Path]:
    names = _normalize_filter_values(source)
    if names is None:
        return sorted(prod_dir.glob("source=*"))
    return [prod_dir / f"source={s}" for s in sorted(dict.fromkeys(names))]


def _production_files(
    root: Path,
    source: str | Iterable[str] | None,
    start: date | None,
    end: date | None,
) -> list[Path]:
    prod_dir = store_dir(root) / "production"
    if not prod_dir.exists():
        return []
    files: list[Path] = []
    for src_dir in _source_dirs(prod_dir, source):
        if not src_dir.exists():
            continue
        for yr_dir in sorted(src_dir.glob("year=*")):
            year = int(yr_dir.name.split("=", 1)[1])
            if start is not None and year < start.year:
                continue
            if end is not None and year > end.year:
                continue
            data_file = yr_dir / "data.parquet"
            if data_file.exists():
                files.append(data_file)
    return files


def _parse_date(value: str | date | None) -> date | None:
    if value is None or isinstance(value, date):
        return value
    text = value.strip()
    if len(text) == 7 and text[4] == "-":
        text = f"{text}-01"
    return date.fromisoformat(text)


def _normalize_filter_values(value: str | Iterable[str] | None) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def load_production(
    *,
    source: str | Iterable[str] | None = None,
    entity_type: str | Iterable[str] | None = None,
    entity_id: str | Iterable[str] | None = None,
    field_id: str | Iterable[str] | None = None,
    facility_id: str | Iterable[str] | None = None,
    basin: str | Iterable[str] | None = None,
    region: str | Iterable[str] | None = None,
    environment: str | Iterable[str] | None = None,
    start: str | date | None = None,
    end: str | date | None = None,
    root: str | Path | None = None,
) -> pd.DataFrame:
    """Query production/injection rates from the Parquet store using DuckDB pushdown."""
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    source_vals = _normalize_filter_values(source)
    files = _production_files(resolve_root(root), source_vals, start_d, end_d)
    if not files:
        return schema.empty_production().to_pandas()

    filters: dict[str, list[str] | None] = {
        "source": source_vals,
        "entity_type": _normalize_filter_values(entity_type),
        "entity_id": _normalize_filter_values(entity_id),
        "field_id": _normalize_filter_values(field_id),
        "facility_id": _normalize_filter_values(facility_id),
        "basin": _normalize_filter_values(basin),
        "region": _normalize_filter_values(region),
        "environment": _normalize_filter_values(environment),
    }
    if any(vals is not None and len(vals) == 0 for vals in filters.values()):
        return schema.empty_production().to_pandas()

    with duckdb.connect() as con:
        rel = con.read_parquet([str(f) for f in files])
        return _execute_filtered_production(rel, filters, start_d, end_d)


def _execute_filtered_production(
    rel: duckdb.DuckDBPyRelation,
    filters: dict[str, list[str] | None],
    start_d: date | None,
    end_d: date | None,
) -> pd.DataFrame:
    expr: duckdb.Expression | None = None
    for col, vals in filters.items():
        if vals is None:
            continue
        cond = ColumnExpression(col).isin(*[ConstantExpression(v) for v in vals])
        expr = cond if expr is None else (expr & cond)
    if start_d is not None:
        cond = ColumnExpression("period") >= ConstantExpression(start_d)
        expr = cond if expr is None else (expr & cond)
    if end_d is not None:
        cond = ColumnExpression("period") <= ConstantExpression(end_d)
        expr = cond if expr is None else (expr & cond)
    if expr is not None:
        rel = rel.filter(expr)
    return rel.order("source, entity_type, entity_id, field_id, facility_id, period").df()


def _register_store_views(con: duckdb.DuckDBPyConnection, root: Path) -> None:
    prod_files = _production_files(root, None, None, None)
    if prod_files:
        con.register("production", con.read_parquet([str(f) for f in prod_files]))
    else:
        con.register("production", schema.empty_production())

    for kind in schema.GeoKind:
        folder = store_dir(root) / "geo" / kind.value
        geo_files = sorted(folder.glob("*.parquet")) if folder.exists() else []
        if geo_files:
            con.register(kind.value, con.read_parquet([str(f) for f in geo_files]))
        else:
            con.register(kind.value, pd.DataFrame(columns=list(schema.GEO_SPECS[kind].columns)))


def sql(query: str, root: str | Path | None = None) -> pd.DataFrame:
    """Run a DuckDB SQL query over ``production``, ``fields``, ``facilities``, and ``wells``."""
    base_root = resolve_root(root)
    with duckdb.connect() as con:
        _register_store_views(con, base_root)
        return con.execute(query).df()


@dataclass(frozen=True)
class IngestSummary:
    """Summary of one :meth:`Store.ingest` run."""

    source: str
    files_ingested: int
    files_skipped: int
    production_rows: int
    partitions: tuple[Path, ...]
    geo_counts: dict[str, int]


def _should_skip_remote(
    remote: base.RemoteFile,
    recorded_files: dict[str, Any],
    *,
    refresh: bool,
    current_year: int,
) -> bool:
    if refresh or remote.key not in recorded_files:
        return False
    return remote.year_max is not None and remote.year_max < current_year - 1


def _resolve_refresh_replacement(
    *,
    refresh: bool,
    years: list[int] | None,
    options: dict[str, Any],
) -> tuple[bool, set[int] | None]:
    """Decide which year partitions can be cleanly replaced without losing partial-filter rows."""
    if not refresh or options.get("environment") is not None:
        return False, None
    if years is not None:
        return True, set(years)
    if options.get("recent"):
        current_year = datetime.now(tz=UTC).year
        return True, {current_year - 1, current_year}
    return True, None


def _discover_for_years(
    connector: base.Source,
    http: httpx2.Client,
    years: list[int] | None,
    options: dict[str, Any],
) -> list[base.RemoteFile]:
    if years is None:
        return connector.discover_production(client=http, year=None, **options)
    seen: set[str] = set()
    remotes: list[base.RemoteFile] = []
    for y in years:
        for remote in connector.discover_production(client=http, year=y, **options):
            if remote.key not in seen:
                seen.add(remote.key)
                remotes.append(remote)
    return remotes


class Store:
    """Primary interface to ogprod's local Parquet/GeoParquet store."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = resolve_root(root)

    def __repr__(self) -> str:
        return f"Store({str(self.root)!r})"

    def manifest(self) -> dict[str, Any]:
        """Return the current store manifest."""
        return load_manifest(self.root)

    def ingest(
        self,
        source: str,
        *,
        year: int | Iterable[int] | None = None,
        origin: Literal["mirror", "publisher"] = "mirror",
        mirror: str | None = None,
        revision: str = "main",
        refresh: bool = False,
        geo_layers: bool = True,
        keep_raw: bool = False,
        files: Sequence[str | Path] | None = None,
        client: httpx2.Client | None = None,
        **options: Any,
    ) -> IngestSummary:
        """Download, normalize, or mirror-sync a source's production and spatial tables."""
        connector = sources.get(source)
        if files is not None and (year is not None or options):
            raise TypeError("omit fetch filters when ingesting local files=")
        if origin not in {"mirror", "publisher"}:
            raise ValueError(f"origin must be 'mirror' or 'publisher', got {origin!r}")

        manifest = load_manifest(self.root)
        src_entry = manifest.setdefault("sources", {}).setdefault(source, {"files": {}})
        src_entry["country"] = connector.country
        src_entry["country_code"] = connector.country_code
        src_entry["publisher"] = connector.publisher
        src_entry["docs_url"] = connector.docs_url

        if files is None and origin == "mirror":
            return self._ingest_from_mirror(
                source=source,
                manifest=manifest,
                src_entry=src_entry,
                year=year,
                mirror_spec=mirror,
                revision=revision,
                geo_layers=geo_layers,
                refresh=refresh,
                keep_raw=keep_raw,
                client=client,
                options=options,
            )

        with tempfile.TemporaryDirectory(prefix="ogprod-") as tmp:
            work_dir = (self.root / "raw" / source) if keep_raw else Path(tmp)
            ingested, skipped, rows, partitions = self._ingest_production_layer(
                connector,
                work_dir=work_dir,
                src_entry=src_entry,
                year=year,
                refresh=refresh,
                files=files,
                client=client,
                options=options,
            )
            geo_counts = (
                self._ingest_geo_layers(connector, work_dir, refresh=refresh, client=client)
                if geo_layers
                else {}
            )

        src_entry["updated_at"] = datetime.now(tz=UTC).isoformat()
        save_manifest(manifest, self.root)
        return IngestSummary(
            source=source,
            files_ingested=ingested,
            files_skipped=skipped,
            production_rows=rows,
            partitions=tuple(dict.fromkeys(partitions)),
            geo_counts=geo_counts,
        )

    def _ingest_from_mirror(
        self,
        *,
        source: str,
        manifest: dict[str, Any],
        src_entry: dict[str, Any],
        year: int | Iterable[int] | None,
        mirror_spec: str | None,
        revision: str,
        geo_layers: bool,
        refresh: bool,
        keep_raw: bool,
        client: httpx2.Client | None,
        options: dict[str, Any],
    ) -> IngestSummary:
        if keep_raw or options:
            unsupported = sorted((*options, *(["keep_raw"] if keep_raw else [])))
            raise ValueError(
                f"options {unsupported} are only supported with origin='publisher', "
                "not origin='mirror'"
            )
        result = mirror_mod.sync_source(
            source,
            store_dir(self.root),
            year=year,
            mirror=mirror_spec,
            revision=revision,
            geo_layers=geo_layers,
            refresh=refresh,
            client=client,
        )
        src_entry["mirror"] = result.mirror
        src_entry["revision"] = result.revision
        src_entry["updated_at"] = datetime.now(tz=UTC).isoformat()
        save_manifest(manifest, self.root)
        return IngestSummary(
            source=source,
            files_ingested=result.files_downloaded,
            files_skipped=result.files_skipped,
            production_rows=result.production_rows,
            partitions=result.partitions,
            geo_counts=result.geo_counts,
        )

    def _ingest_production_layer(
        self,
        connector: base.Source,
        *,
        work_dir: Path,
        src_entry: dict[str, Any],
        year: int | Iterable[int] | None,
        refresh: bool,
        files: Sequence[str | Path] | None,
        client: httpx2.Client | None,
        options: dict[str, Any],
    ) -> tuple[int, int, int, list[Path]]:
        if files is not None:
            return self._ingest_local_files(connector, [Path(f) for f in files])
        years = mirror_mod.normalize_years(year)
        replace_mode, replace_years = _resolve_refresh_replacement(
            refresh=refresh, years=years, options=options
        )
        with base.open_client(client) as http:
            remotes = _discover_for_years(connector, http, years, options)
            return self._ingest_remote_files(
                connector,
                remotes,
                http=http,
                work_dir=work_dir / "production",
                recorded=src_entry.setdefault("files", {}),
                refresh=refresh,
                replace_mode=replace_mode,
                replace_years=replace_years,
            )

    def _ingest_local_files(
        self, connector: base.Source, paths: list[Path]
    ) -> tuple[int, int, int, list[Path]]:
        batch: list[pa.Table] = []
        rows = 0
        partitions: list[Path] = []
        for path in paths:
            table = connector.parse_production(path)
            batch.append(table)
            if sum(t.num_rows for t in batch) >= _BATCH_ROWS:
                rows, partitions = self._flush_batch(batch, rows, partitions)
                batch = []
        if batch:
            rows, partitions = self._flush_batch(batch, rows, partitions)
        return len(paths), 0, rows, partitions

    def _ingest_remote_files(
        self,
        connector: base.Source,
        remotes: list[base.RemoteFile],
        *,
        http: httpx2.Client,
        work_dir: Path,
        recorded: dict[str, Any],
        refresh: bool,
        replace_mode: bool = False,
        replace_years: set[int] | None = None,
    ) -> tuple[int, int, int, list[Path]]:
        current_year = datetime.now(tz=UTC).year
        ingested = 0
        skipped = 0
        rows = 0
        partitions: list[Path] = []
        batch: list[pa.Table] = []
        already_replaced: set[Path] | None = set() if replace_mode else None

        for remote in remotes:
            if _should_skip_remote(remote, recorded, refresh=refresh, current_year=current_year):
                skipped += 1
                continue
            dest = work_dir / remote.key
            dl = connector.fetch_production(remote, dest, client=http)
            table = connector.parse_production(dl.path)
            batch.append(table)
            recorded[remote.key] = {
                "url": dl.url,
                "sha256": dl.sha256,
                "bytes": dl.size_bytes,
                "ingested_at": datetime.now(tz=UTC).isoformat(),
            }
            ingested += 1
            if sum(t.num_rows for t in batch) >= _BATCH_ROWS:
                rows, partitions = self._flush_batch(
                    batch,
                    rows,
                    partitions,
                    replace_years=replace_years,
                    already_replaced=already_replaced,
                )
                batch = []
        if batch:
            rows, partitions = self._flush_batch(
                batch,
                rows,
                partitions,
                replace_years=replace_years,
                already_replaced=already_replaced,
            )
        return ingested, skipped, rows, partitions

    def _flush_batch(
        self,
        batch: list[pa.Table],
        rows: int,
        partitions: list[Path],
        *,
        replace_years: set[int] | None = None,
        already_replaced: set[Path] | None = None,
    ) -> tuple[int, list[Path]]:
        combined = combine_tables(batch)
        partitions.extend(
            _write_production_parts(
                combined,
                root=self.root,
                replace_years=replace_years,
                already_replaced=already_replaced,
            )
        )
        return rows + combined.num_rows, partitions

    def _ingest_geo_layers(
        self,
        connector: base.Source,
        work_dir: Path,
        *,
        refresh: bool,
        client: httpx2.Client | None,
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        with base.open_client(client) as http:
            for kind in schema.GeoKind:
                target = _geo_path(self.root, kind, connector.name)
                if target.exists() and not refresh:
                    continue
                try:
                    downloads = connector.fetch_geo(
                        kind, work_dir / "geo" / kind.value, client=http
                    )
                except NotImplementedError:
                    continue
                if not downloads:
                    continue
                gdf = connector.parse_geo(kind, [d.path for d in downloads])
                write_geo(gdf, kind, root=self.root)
                counts[kind.value] = len(gdf)
        return counts

    def production(self, **filters: Any) -> pd.DataFrame:
        """Query canonical production/injection rates."""
        return load_production(root=self.root, **filters)

    def fields(self, *, source: str | None = None) -> gpd.GeoDataFrame:
        """Load field polygon GeoParquet table."""
        return load_geo(schema.GeoKind.FIELDS, source=source, root=self.root)

    def facilities(self, *, source: str | None = None) -> gpd.GeoDataFrame:
        """Load facility (platform/FPSO) point GeoParquet table."""
        return load_geo(schema.GeoKind.FACILITIES, source=source, root=self.root)

    def wells(self, *, source: str | None = None) -> gpd.GeoDataFrame:
        """Load well point GeoParquet table."""
        return load_geo(schema.GeoKind.WELLS, source=source, root=self.root)

    def sql(self, query: str) -> pd.DataFrame:
        """Run a DuckDB SQL query over ``production``, ``fields``, ``facilities``, and ``wells``."""
        return sql(query, root=self.root)

    def coverage(self) -> pd.DataFrame:
        """Summarize production rows, periods, and spatial entity counts per source."""
        return self.sql(
            """
            WITH p AS (
                SELECT source,
                       count(*) AS rows,
                       count(DISTINCT entity_id) AS entities,
                       min(period) AS first_period,
                       max(period) AS last_period
                FROM production
                GROUP BY source
            ),
            f AS (SELECT source, count(*) AS fields FROM fields GROUP BY source),
            fac AS (SELECT source, count(*) AS facilities FROM facilities GROUP BY source),
            w AS (SELECT source, count(*) AS wells FROM wells GROUP BY source)
            SELECT coalesce(p.source, f.source, fac.source, w.source) AS source,
                   coalesce(p.rows, 0) AS rows,
                   coalesce(p.entities, 0) AS producing_entities,
                   p.first_period,
                   p.last_period,
                   coalesce(f.fields, 0) AS fields,
                   coalesce(fac.facilities, 0) AS facilities,
                   coalesce(w.wells, 0) AS wells
            FROM p
            FULL OUTER JOIN f ON p.source = f.source
            FULL OUTER JOIN fac ON coalesce(p.source, f.source) = fac.source
            FULL OUTER JOIN w ON coalesce(p.source, f.source, fac.source) = w.source
            ORDER BY 1
            """
        )

    def audit(
        self,
        *,
        source: str | None = None,
        reference_date: date | None = None,
        stale_months: int = 6,
    ) -> AuditReport:
        """Audit stored Parquet and GeoParquet tables for data-quality issues."""
        from ogprod.audit import audit_store

        return audit_store(
            self.root,
            source=source,
            reference_date=reference_date,
            stale_months=stale_months,
        )
