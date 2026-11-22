"""Canonical Parquet mirror layout, manifest v1, verified sync, and publish helpers.

Readers (``Store.ingest(..., origin="mirror")``) talk to the mirror over plain
anonymous HTTPS via ``httpx2`` — no Hugging Face token and no
``huggingface_hub`` dependency. Maintainer publishing (``scripts/publish_mirror.py``)
reuses the manifest, stats, sanity-gate, and merge helpers here.
"""

from __future__ import annotations

import copy
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import duckdb
import httpx2
import pyarrow.parquet as pq

DEFAULT_MIRROR = "mralbu/ogprod"
ENV_VAR = "OGPROD_MIRROR"
SUPPORTED_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.json"
HF_DATASETS_BASE_URL = "https://huggingface.co/datasets"

#: Reject production partitions whose row count or any product total drops more than 15%.
MAX_PRODUCTION_DROP = 0.15
#: Reject entities files whose row count drops more than 10%.
MAX_ENTITIES_DROP = 0.10

_CHUNK_BYTES = 1024 * 1024


def utc_now_iso() -> str:
    """Current UTC timestamp in ISO-8601 ``Z`` format."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_mirror(mirror: str | None = None) -> str:
    """Resolve the mirror base URL from ``mirror=``, ``$OGPROD_MIRROR``, or ``DEFAULT_MIRROR``.

    Accepts either a Hugging Face dataset repo id (e.g. ``"mralbu/ogprod"``) or
    an explicit ``http://``/``https://`` base URL for tests and custom mirrors.
    """
    target = mirror or os.environ.get(ENV_VAR) or DEFAULT_MIRROR
    target = target.strip()
    if not target:
        target = DEFAULT_MIRROR
    if target.startswith(("http://", "https://")):
        return target.rstrip("/")
    return f"{HF_DATASETS_BASE_URL}/{target.strip('/')}"


def resolve_repo_id(repo: str | None = None) -> str:
    """Resolve the Hugging Face dataset repo id (``owner/name``) for publishing."""
    target = (repo or os.environ.get(ENV_VAR) or DEFAULT_MIRROR).strip()
    if not target:
        target = DEFAULT_MIRROR
    prefix = f"{HF_DATASETS_BASE_URL}/"
    if target.startswith(prefix):
        target = target[len(prefix) :].strip("/")
    if target.startswith(("http://", "https://")):
        raise ValueError(
            f"cannot publish to non-Hugging Face mirror URL {target!r}; "
            "pass a dataset repo id like 'owner/repo' via --repo"
        )
    if target.count("/") != 1 or any(not part for part in target.split("/")):
        raise ValueError(f"invalid Hugging Face dataset repo id {target!r}; expected 'owner/repo'")
    return target


def file_url(mirror_base: str, revision: str, relative_path: str) -> str:
    """Build the anonymous HTTPS URL for ``relative_path`` at ``revision``."""
    encoded_rev = quote(revision, safe="")
    encoded_path = quote(relative_path.lstrip("/"), safe="/=")
    return f"{mirror_base.rstrip('/')}/resolve/{encoded_rev}/{encoded_path}"


def empty_manifest(updated_at: str | None = None) -> dict[str, Any]:
    """Return an empty schema v1 manifest dictionary."""
    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "updated_at": updated_at or utc_now_iso(),
        "sources": {},
    }


def validate_manifest(manifest: Any) -> dict[str, Any]:
    """Validate manifest structure and ``schema_version`` compatibility."""
    if not isinstance(manifest, dict):
        raise ValueError("mirror manifest must be a JSON object")
    version = manifest.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError(f"invalid mirror manifest schema_version: {version!r}")
    if version > SUPPORTED_SCHEMA_VERSION:
        raise RuntimeError(
            f"mirror manifest schema_version {version} is newer than supported "
            f"version {SUPPORTED_SCHEMA_VERSION}; please upgrade ogprod"
        )
    sources = manifest.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("mirror manifest 'sources' field must be a JSON object")
    return manifest


def validate_relative_path(root: Path, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``root`` and reject path traversal or absolute paths."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError(f"invalid manifest path {relative_path!r}")
    if "\\" in relative_path:
        raise ValueError(f"unsafe manifest path {relative_path!r}: backslashes are not allowed")
    posix = PurePosixPath(relative_path)
    if posix.is_absolute() or any(part in ("", ".", "..") for part in posix.parts):
        raise ValueError(f"unsafe manifest path {relative_path!r}: must stay inside store root")
    resolved_root = root.resolve()
    target = (resolved_root / Path(*posix.parts)).resolve()
    if target == resolved_root or not target.is_relative_to(resolved_root):
        raise ValueError(f"unsafe manifest path {relative_path!r}: escapes store root")
    return root / Path(*posix.parts)


def sha256_file(path: Path) -> str:
    """Compute the lowercase hex SHA-256 digest of ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_manifest(client: httpx2.Client, mirror_base: str, revision: str) -> dict[str, Any]:
    """Fetch and validate ``manifest.json`` from ``mirror_base`` at ``revision``."""
    url = file_url(mirror_base, revision, MANIFEST_FILENAME)
    response = client.get(url)
    response.raise_for_status()
    return validate_manifest(response.json())


def download_verified(
    client: httpx2.Client,
    url: str,
    target: Path,
    *,
    expected_sha256: str,
    expected_bytes: int,
) -> Path:
    """Stream ``url`` to a temp file, verify byte length and SHA-256, and atomically replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    digest = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with tmp.open("wb") as sink:
                for chunk in response.iter_bytes():
                    sink.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        if size != expected_bytes:
            raise RuntimeError(
                f"corrupted download for {target.name}: expected {expected_bytes} bytes, got {size}"
            )
        actual_sha256 = digest.hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"checksum mismatch for {target.name}: "
                f"expected {expected_sha256}, got {actual_sha256}"
            )
        os.replace(tmp, target)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    return target


def production_partition_entry(
    root: Path, path: Path, *, updated_at: str | None = None
) -> dict[str, Any]:
    """Compute the manifest v1 entry for a local production partition Parquet file."""
    rel_path = path.relative_to(root).as_posix()
    file_bytes = path.stat().st_size
    file_sha256 = sha256_file(path)

    with duckdb.connect() as con:
        summary = con.execute(
            "SELECT count(*), min(period), max(period) FROM read_parquet(?)",
            [str(path)],
        ).fetchone()
        assert summary is not None
        rows = int(summary[0])
        first_period = summary[1].strftime("%Y-%m-%d") if summary[1] is not None else None
        last_period = summary[2].strftime("%Y-%m-%d") if summary[2] is not None else None

        product_rows = con.execute(
            "SELECT product, sum(volume) FROM read_parquet(?) GROUP BY product ORDER BY product",
            [str(path)],
        ).fetchall()
        totals_m3 = {str(product): float(total) for product, total in product_rows}

    return {
        "path": rel_path,
        "sha256": file_sha256,
        "bytes": file_bytes,
        "rows": rows,
        "first_period": first_period,
        "last_period": last_period,
        "totals_m3": totals_m3,
        "updated_at": updated_at or utc_now_iso(),
    }


def entities_entry(root: Path, path: Path, *, updated_at: str | None = None) -> dict[str, Any]:
    """Compute the manifest v1 entry for a local entities GeoParquet file."""
    rel_path = path.relative_to(root).as_posix()
    file_bytes = path.stat().st_size
    file_sha256 = sha256_file(path)
    rows = int(pq.read_metadata(path).num_rows)
    return {
        "path": rel_path,
        "sha256": file_sha256,
        "bytes": file_bytes,
        "rows": rows,
        "updated_at": updated_at or utc_now_iso(),
    }


@dataclass(frozen=True)
class GateViolation:
    """One failed publish sanity-gate check."""

    source: str
    target: str
    reason: str

    def __str__(self) -> str:
        return f"{self.source}/{self.target}: {self.reason}"


def check_production_entry(
    source: str,
    year: str,
    new: dict[str, Any],
    old: dict[str, Any] | None,
) -> list[GateViolation]:
    """Run sanity-gate checks on a candidate production partition."""
    violations: list[GateViolation] = []
    new_rows = int(new["rows"])
    if new_rows == 0:
        violations.append(GateViolation(source, f"year={year}", "empty production partition"))
        return violations
    if old is None:
        return violations

    old_rows = int(old.get("rows", 0))
    min_rows = (1.0 - MAX_PRODUCTION_DROP) * old_rows
    if old_rows > 0 and new_rows < min_rows:
        drop_pct = (1.0 - new_rows / old_rows) * 100.0
        violations.append(
            GateViolation(
                source,
                f"year={year}",
                f"row count dropped {drop_pct:.1f}% ({old_rows:,} -> {new_rows:,})",
            )
        )

    old_totals: dict[str, float] = old.get("totals_m3") or {}
    new_totals: dict[str, float] = new.get("totals_m3") or {}
    for product, old_total in sorted(old_totals.items()):
        old_val = float(old_total)
        if old_val <= 0:
            continue
        new_val = float(new_totals.get(product, 0.0))
        if new_val < (1.0 - MAX_PRODUCTION_DROP) * old_val:
            drop_pct = (1.0 - new_val / old_val) * 100.0
            violations.append(
                GateViolation(
                    source,
                    f"year={year}",
                    (
                        f"{product} total dropped {drop_pct:.1f}% "
                        f"({old_val:,.1f} -> {new_val:,.1f} m3)"
                    ),
                )
            )
    return violations


def check_entities_entry(
    source: str,
    new: dict[str, Any],
    old: dict[str, Any] | None,
) -> list[GateViolation]:
    """Run sanity-gate checks on a candidate entities file."""
    violations: list[GateViolation] = []
    new_rows = int(new["rows"])
    if new_rows == 0:
        violations.append(GateViolation(source, "entities", "empty entities file"))
        return violations
    if old is None:
        return violations

    old_rows = int(old.get("rows", 0))
    if old_rows > 0 and new_rows < (1.0 - MAX_ENTITIES_DROP) * old_rows:
        drop_pct = (1.0 - new_rows / old_rows) * 100.0
        violations.append(
            GateViolation(
                source,
                "entities",
                f"entity count dropped {drop_pct:.1f}% ({old_rows:,} -> {new_rows:,})",
            )
        )
    return violations


def merge_manifest(
    remote: dict[str, Any],
    updates: dict[str, dict[str, Any]],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    """Merge changed source entries into a copy of ``remote`` without deleting untouched entries.

    ``updates`` maps ``source`` to a dict with optional ``"production"``
    (mapping year strings to entries) and/or ``"entities"`` (an entry dict).
    """
    merged = copy.deepcopy(validate_manifest(remote))
    merged["schema_version"] = SUPPORTED_SCHEMA_VERSION
    merged["updated_at"] = updated_at or utc_now_iso()
    sources: dict[str, Any] = merged["sources"]

    for source, change in updates.items():
        target = sources.setdefault(source, {})
        if "production" in change and change["production"]:
            prod = dict(target.get("production", {}))
            prod.update(change["production"])
            target["production"] = dict(sorted(prod.items(), key=lambda kv: int(kv[0])))
        if "entities" in change and change["entities"] is not None:
            target["entities"] = change["entities"]

    merged["sources"] = dict(sorted(sources.items()))
    return merged
