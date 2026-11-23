"""Anonymous HTTPS mirror client, v2 manifest contract, and publish sanity gates.

By default :meth:`ogprod.Store.ingest` downloads pre-normalized Parquet and
GeoParquet 1.1 files from the public Hugging Face dataset mirror
(``mralbu/ogprod``) using plain :mod:`httpx2` with no token and no
``huggingface_hub`` runtime dependency.
"""

from __future__ import annotations

import copy
import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx2
import numpy as np
import pyarrow.parquet as pq

from ogprod import schema
from ogprod.sources import base

#: Default Hugging Face dataset repository hosting the canonical v2 mirror.
DEFAULT_MIRROR = "mralbu/ogprod"

#: Environment variable overriding :data:`DEFAULT_MIRROR`.
MIRROR_ENV_VAR = "OGPROD_MIRROR"

#: Supported mirror manifest schema version.
MANIFEST_SCHEMA_VERSION = 2

_HF_DATASETS_BASE = "https://huggingface.co/datasets"
_CHUNK_BYTES = 1024 * 1024
_ALLOWED_ROOT_DIRS = frozenset({"production", "geo"})


class MirrorError(ValueError):
    """Raised when a mirror manifest, path, download, or checksum is invalid."""


@dataclass(frozen=True)
class MirrorSyncResult:
    """Summary of syncing one source from a mirror into the local store."""

    source: str
    mirror: str
    revision: str
    files_downloaded: int
    files_skipped: int
    production_rows: int
    partitions: tuple[Path, ...]
    geo_counts: dict[str, int]


def resolve_mirror(mirror: str | None = None) -> str:
    """Resolve the mirror identifier: explicit arg -> ``$OGPROD_MIRROR`` -> default."""
    if mirror is not None and mirror.strip():
        return mirror.strip()
    if env := os.environ.get(MIRROR_ENV_VAR, "").strip():
        return env
    return DEFAULT_MIRROR


def mirror_base_url(mirror: str | None = None, *, revision: str = "main") -> str:
    """Return the anonymous HTTPS base URL for resolving files from ``mirror``."""
    resolved = resolve_mirror(mirror)
    if resolved.startswith(("https://", "http://")):
        return resolved.rstrip("/")
    parts = [p for p in resolved.split("/") if p]
    if len(parts) != 2 or any(p in {".", ".."} or "\\" in p for p in parts):
        raise MirrorError(
            f"invalid mirror repository {resolved!r}; expected 'owner/repo' or an HTTP(S) URL"
        )
    if not revision or any(ch in revision for ch in ("..", "\\", " ", "\n", "\r")):
        raise MirrorError(f"invalid mirror revision: {revision!r}")
    return f"{_HF_DATASETS_BASE}/{parts[0]}/{parts[1]}/resolve/{revision}"


def validate_rel_path(rel_path: str, *, store_root: Path | None = None) -> PurePosixPath:
    """Validate that a manifest-supplied relative path cannot traverse outside the store."""
    if not rel_path or "\\" in rel_path or "\x00" in rel_path:
        raise MirrorError(f"unsafe mirror path: {rel_path!r}")
    posix = PurePosixPath(rel_path)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise MirrorError(f"path traversal rejected in mirror path: {rel_path!r}")
    if posix.parts[0] not in _ALLOWED_ROOT_DIRS and rel_path != "manifest.json":
        raise MirrorError(f"unexpected mirror path prefix: {rel_path!r}")
    if store_root is not None:
        resolved_root = store_root.resolve()
        resolved_dest = (resolved_root / posix).resolve()
        if not resolved_dest.is_relative_to(resolved_root):
            raise MirrorError(f"mirror path escapes store root: {rel_path!r}")
    return posix


def mirror_file_url(
    rel_path: str,
    *,
    mirror: str | None = None,
    revision: str = "main",
) -> str:
    """Build the full anonymous HTTPS URL for ``rel_path`` in ``mirror``."""
    safe = validate_rel_path(rel_path)
    return f"{mirror_base_url(mirror, revision=revision)}/{safe.as_posix()}"


def normalize_years(year: int | Iterable[int] | None) -> list[int] | None:
    """Normalize a single year, iterable of years, or ``None`` into sorted unique ints."""
    if year is None:
        return None
    if isinstance(year, bool):
        raise TypeError("year must be an int or iterable of ints, not bool")
    if isinstance(year, int):
        return [year]
    years: set[int] = set()
    for item in year:
        if isinstance(item, bool) or not isinstance(item, int):
            raise TypeError(f"year entries must be ints, got {type(item).__name__}")
        years.add(item)
    return sorted(years)


def sha256_file(path: Path) -> tuple[str, int]:
    """Return ``(sha256_hex, size_bytes)`` for an existing file."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _is_local_unchanged(dest: Path, expected_bytes: int, expected_sha256: str) -> bool:
    if not dest.exists():
        return False
    if dest.stat().st_size != expected_bytes:
        return False
    actual_sha, actual_size = sha256_file(dest)
    return actual_size == expected_bytes and actual_sha == expected_sha256


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate that a fetched mirror manifest matches ``schema_version == 2``."""
    if not isinstance(manifest, dict):
        raise MirrorError("mirror manifest must be a JSON object")
    version = manifest.get("schema_version")
    if version != MANIFEST_SCHEMA_VERSION:
        raise MirrorError(
            f"unsupported mirror manifest schema_version={version!r} "
            f"(expected {MANIFEST_SCHEMA_VERSION})"
        )
    if not isinstance(manifest.get("sources"), dict):
        raise MirrorError("mirror manifest missing 'sources' mapping")
    return manifest


def fetch_manifest(
    client: httpx2.Client,
    *,
    mirror: str | None = None,
    revision: str = "main",
) -> dict[str, Any]:
    """Fetch and validate ``manifest.json`` from the mirror over anonymous HTTPS."""
    url = mirror_file_url("manifest.json", mirror=mirror, revision=revision)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx2.HTTPError, ValueError) as err:
        raise MirrorError(f"failed to fetch mirror manifest from {url}: {err}") from err
    return validate_manifest(payload)


def download_verified(
    client: httpx2.Client,
    url: str,
    dest: Path,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    """Stream ``url`` to ``dest.tmp``, verify size & SHA-256, and atomically replace ``dest``."""
    if expected_bytes < 0 or not expected_sha256:
        raise MirrorError(f"invalid mirror entry checksum metadata for {dest.name}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.tmp")
    digest = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with tmp.open("wb") as sink:
                for chunk in resp.iter_bytes():
                    sink.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        _verify_digest(url, size, digest.hexdigest(), expected_bytes, expected_sha256)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _verify_digest(
    url: str,
    actual_bytes: int,
    actual_sha256: str,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    if actual_bytes != expected_bytes:
        raise MirrorError(
            f"byte size mismatch for {url}: expected {expected_bytes}, got {actual_bytes}"
        )
    if actual_sha256 != expected_sha256:
        raise MirrorError(
            f"sha256 mismatch for {url}: expected {expected_sha256}, got {actual_sha256}"
        )


def _select_production_entries(
    prod_map: dict[str, Any],
    years: list[int] | None,
    *,
    source: str,
) -> list[tuple[int, dict[str, Any]]]:
    available = {int(k): v for k, v in prod_map.items()}
    if years is None:
        return sorted(available.items())
    missing = [y for y in years if y not in available]
    if missing:
        raise MirrorError(
            f"source {source!r} mirror does not contain year(s) {missing}; "
            f"available: {sorted(available)}"
        )
    return [(y, available[y]) for y in years]


def _sync_entry(
    http: httpx2.Client,
    entry: dict[str, Any],
    store_root: Path,
    *,
    mirror: str,
    revision: str,
    refresh: bool,
) -> tuple[Path, bool]:
    rel = validate_rel_path(str(entry["path"]), store_root=store_root)
    dest = store_root / rel
    expected_bytes = int(entry["bytes"])
    expected_sha = str(entry["sha256"])
    if not refresh and _is_local_unchanged(dest, expected_bytes, expected_sha):
        return dest, False
    url = mirror_file_url(rel.as_posix(), mirror=mirror, revision=revision)
    download_verified(
        http,
        url,
        dest,
        expected_bytes=expected_bytes,
        expected_sha256=expected_sha,
    )
    return dest, True


def _sync_geo_entries(
    http: httpx2.Client,
    geo_map: dict[str, Any],
    store_root: Path,
    *,
    mirror: str,
    revision: str,
    refresh: bool,
) -> tuple[int, int, dict[str, int]]:
    downloaded = 0
    skipped = 0
    counts: dict[str, int] = {}
    valid_kinds = {k.value for k in schema.GeoKind}
    for kind_name, entry in sorted(geo_map.items()):
        if kind_name not in valid_kinds:
            raise MirrorError(f"unknown geo layer {kind_name!r} in mirror manifest")
        _, did_download = _sync_entry(
            http,
            entry,
            store_root,
            mirror=mirror,
            revision=revision,
            refresh=refresh,
        )
        if did_download:
            downloaded += 1
        else:
            skipped += 1
        counts[kind_name] = int(entry.get("rows", 0))
    return downloaded, skipped, counts


def sync_source(
    source: str,
    store_root: Path,
    *,
    year: int | Iterable[int] | None = None,
    mirror: str | None = None,
    revision: str = "main",
    geo_layers: bool = True,
    refresh: bool = False,
    client: httpx2.Client | None = None,
) -> MirrorSyncResult:
    """Sync one source's Parquet partitions and GeoParquet layers from a v2 mirror."""
    resolved = resolve_mirror(mirror)
    years = normalize_years(year)

    with base.open_client(client) as http:
        manifest = fetch_manifest(http, mirror=resolved, revision=revision)
        sources_map = manifest["sources"]
        if source not in sources_map:
            avail = ", ".join(sorted(sources_map)) or "(none)"
            raise MirrorError(f"source {source!r} not found in mirror {resolved!r}; found: {avail}")
        src_meta = sources_map[source]

        selected = _select_production_entries(
            src_meta.get("production", {}),
            years,
            source=source,
        )
        downloaded = 0
        skipped = 0
        prod_rows = 0
        partitions: list[Path] = []
        for _, entry in selected:
            dest, did_download = _sync_entry(
                http,
                entry,
                store_root,
                mirror=resolved,
                revision=revision,
                refresh=refresh,
            )
            if did_download:
                downloaded += 1
            else:
                skipped += 1
            prod_rows += int(entry.get("rows", 0))
            partitions.append(dest)

        geo_counts: dict[str, int] = {}
        if geo_layers:
            g_dl, g_skip, geo_counts = _sync_geo_entries(
                http,
                src_meta.get("geo", {}),
                store_root,
                mirror=resolved,
                revision=revision,
                refresh=refresh,
            )
            downloaded += g_dl
            skipped += g_skip

    return MirrorSyncResult(
        source=source,
        mirror=resolved,
        revision=revision,
        files_downloaded=downloaded,
        files_skipped=skipped,
        production_rows=prod_rows,
        partitions=tuple(partitions),
        geo_counts=geo_counts,
    )


def compute_partition_stats(
    path: Path,
    rel_path: str,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Compute v2 mirror manifest metadata for one production Parquet partition."""
    validate_rel_path(rel_path)
    digest, size_bytes = sha256_file(path)
    table = pq.read_table(path)
    rows = table.num_rows
    first_period: str | None = None
    last_period: str | None = None
    if rows > 0:
        periods = [p for p in table.column("period").to_pylist() if p is not None]
        if periods:
            first_period = min(periods).isoformat()
            last_period = max(periods).isoformat()

    sum_rates: dict[str, float] = {}
    for col in schema.RATE_COLUMNS:
        if col in table.column_names and rows > 0:
            arr = table.column(col).to_numpy(zero_copy_only=False).astype(np.float64, copy=False)
            total = float(np.nansum(arr))
            sum_rates[col] = round(total, 4)
        else:
            sum_rates[col] = 0.0

    return {
        "path": rel_path,
        "sha256": digest,
        "bytes": size_bytes,
        "rows": rows,
        "first_period": first_period,
        "last_period": last_period,
        "sum_rates": sum_rates,
        "updated_at": updated_at or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def compute_geo_stats(
    path: Path,
    rel_path: str,
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Compute v2 mirror manifest metadata for one GeoParquet layer."""
    validate_rel_path(rel_path)
    digest, size_bytes = sha256_file(path)
    rows = pq.read_metadata(path).num_rows
    return {
        "path": rel_path,
        "sha256": digest,
        "bytes": size_bytes,
        "rows": rows,
        "updated_at": updated_at or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _check_production_entry(
    source: str,
    year_key: str,
    new_entry: dict[str, Any],
    old_entry: dict[str, Any] | None,
    max_prod_drop: float,
) -> list[str]:
    issues: list[str] = []
    if new_entry.get("rows", 0) <= 0 or new_entry.get("bytes", 0) <= 0:
        issues.append(f"{source}/{year_key}: empty production partition")
        return issues
    if old_entry is None:
        return issues
    old_rows = int(old_entry.get("rows", 0))
    new_rows = int(new_entry["rows"])
    if old_rows > 0 and new_rows < old_rows * (1.0 - max_prod_drop):
        drop_pct = (1.0 - new_rows / old_rows) * 100.0
        issues.append(
            f"{source}/{year_key}: row count dropped {drop_pct:.1f}% ({old_rows} -> {new_rows})"
        )
    old_rates = old_entry.get("sum_rates") or {}
    new_rates = new_entry.get("sum_rates") or {}
    for col, old_val in old_rates.items():
        old_f = float(old_val)
        new_f = float(new_rates.get(col, 0.0))
        if old_f > 0.0 and new_f < old_f * (1.0 - max_prod_drop):
            drop_pct = (1.0 - new_f / old_f) * 100.0
            issues.append(
                f"{source}/{year_key} {col}: sum rate dropped {drop_pct:.1f}% "
                f"({old_f:.1f} -> {new_f:.1f})"
            )
    return issues


def _check_geo_entry(
    source: str,
    kind: str,
    new_entry: dict[str, Any],
    old_entry: dict[str, Any] | None,
    max_geo_drop: float,
) -> list[str]:
    if new_entry.get("rows", 0) <= 0 or new_entry.get("bytes", 0) <= 0:
        return [f"{source}/geo/{kind}: empty geo file"]
    if old_entry is None:
        return []
    old_rows = int(old_entry.get("rows", 0))
    new_rows = int(new_entry["rows"])
    if old_rows > 0 and new_rows < old_rows * (1.0 - max_geo_drop):
        drop_pct = (1.0 - new_rows / old_rows) * 100.0
        return [
            f"{source}/geo/{kind}: row count dropped {drop_pct:.1f}% ({old_rows} -> {new_rows})"
        ]
    return []


def check_sanity_gate(
    local_sources: dict[str, Any],
    remote_manifest: dict[str, Any] | None,
    *,
    max_prod_drop: float = 0.15,
    max_geo_drop: float = 0.10,
) -> list[str]:
    """Return sanity-gate violations comparing ``local_sources`` against ``remote_manifest``."""
    remote_sources = (
        remote_manifest.get("sources", {})
        if remote_manifest and remote_manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
        else {}
    )
    issues: list[str] = []
    for src_name, src_data in sorted(local_sources.items()):
        remote_src = remote_sources.get(src_name, {})
        remote_prod = remote_src.get("production", {})
        for year_key, entry in sorted(src_data.get("production", {}).items()):
            issues.extend(
                _check_production_entry(
                    src_name, year_key, entry, remote_prod.get(year_key), max_prod_drop
                )
            )
        remote_geo = remote_src.get("geo", {})
        for kind, entry in sorted(src_data.get("geo", {}).items()):
            issues.extend(
                _check_geo_entry(src_name, kind, entry, remote_geo.get(kind), max_geo_drop)
            )
    return issues


def merge_mirror_manifest(
    remote_manifest: dict[str, Any] | None,
    local_sources: dict[str, Any],
    *,
    updated_at: str | None = None,
    reset: bool = False,
) -> dict[str, Any]:
    """Merge changed local entries into a v2 mirror manifest without dropping untouched years."""
    ts = updated_at or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged: dict[str, Any]
    if (
        not reset
        and remote_manifest is not None
        and remote_manifest.get("schema_version") == MANIFEST_SCHEMA_VERSION
    ):
        merged = copy.deepcopy(remote_manifest)
    else:
        merged = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "units": dict(schema.RATE_UNITS),
            "sources": {},
        }
    merged["schema_version"] = MANIFEST_SCHEMA_VERSION
    merged["units"] = dict(schema.RATE_UNITS)
    merged["updated_at"] = ts

    sources_out: dict[str, Any] = merged.setdefault("sources", {})
    for src_name, src_data in sorted(local_sources.items()):
        target: dict[str, Any] = sources_out.setdefault(src_name, {"production": {}, "geo": {}})
        for meta_key in ("country", "country_code", "publisher", "docs_url", "entity_type"):
            if meta_key in src_data:
                target[meta_key] = src_data[meta_key]
        target["updated_at"] = src_data.get("updated_at", ts)
        prod_target: dict[str, Any] = target.setdefault("production", {})
        for year_key, entry in src_data.get("production", {}).items():
            prod_target[str(year_key)] = copy.deepcopy(entry)
        geo_target: dict[str, Any] = target.setdefault("geo", {})
        for kind, entry in src_data.get("geo", {}).items():
            geo_target[str(kind)] = copy.deepcopy(entry)
    return merged
