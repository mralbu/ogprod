"""Publish local ogprod store partitions and entities to a Hugging Face Dataset mirror.

Used identically from a maintainer laptop (``HF_TOKEN``) and from GitHub Actions
(``secrets.HF_TOKEN``). Computes manifest v1 stats and SHA-256 digests, skips
byte-identical files, enforces the sanity gate (unless ``--force``), merges only
changed entries into the remote ``manifest.json`` without touching other
sources/years, and pushes everything in one atomic commit with
``parent_commit`` optimistic locking.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from huggingface_hub import CommitOperationAdd, HfApi
from huggingface_hub.errors import EntryNotFoundError

from ogprod import mirror, store


class SanityGateError(RuntimeError):
    """Raised when one or more candidate mirror files fail the publish sanity gate."""

    def __init__(self, violations: list[mirror.GateViolation]) -> None:
        self.violations = list(violations)
        details = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"publish sanity gate failed with {len(self.violations)} violation(s) "
            f"(pass --force to override):\n{details}"
        )


@dataclass(frozen=True)
class PlannedChange:
    """One local file that differs from the remote manifest."""

    source: str
    kind: str  # "production" or "entities"
    target: str  # "year=2024" or "entities"
    year: str | None
    local_path: Path
    new_entry: dict[str, Any]
    old_entry: dict[str, Any] | None
    violations: tuple[mirror.GateViolation, ...]


@dataclass(frozen=True)
class PublishPlan:
    """Computed diff and sanity-gate outcome against the remote manifest."""

    repo_id: str
    remote_oid: str | None
    remote_manifest: dict[str, Any]
    changes: tuple[PlannedChange, ...]
    skipped: int

    @property
    def violations(self) -> list[mirror.GateViolation]:
        return [v for change in self.changes for v in change.violations]

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True)
class PublishResult:
    """Result of running :func:`publish_mirror`."""

    repo_id: str
    changed: bool
    dry_run: bool
    commit_oid: str | None
    tag: str | None
    changes: tuple[PlannedChange, ...]
    skipped: int
    merged_manifest: dict[str, Any]


def discover_sources(root: Path) -> list[str]:
    """Return sorted source names present under ``root/production`` or ``root/entities``."""
    found: set[str] = set()
    prod_dir = root / "production"
    if prod_dir.exists():
        for source_dir in prod_dir.glob("source=*"):
            if source_dir.is_dir():
                found.add(source_dir.name.split("=", 1)[1])
    entities_dir = root / "entities"
    if entities_dir.exists():
        for file in entities_dir.glob("*.parquet"):
            if file.is_file():
                found.add(file.stem)
    return sorted(found)


def fetch_remote_state(api: Any, repo_id: str) -> tuple[str | None, dict[str, Any]]:
    """Fetch ``main`` commit OID and remote ``manifest.json`` (or an empty v1 manifest)."""
    info = api.repo_info(repo_id=repo_id, repo_type="dataset", revision="main")
    remote_oid: str | None = getattr(info, "sha", None)
    try:
        local_manifest = api.hf_hub_download(
            repo_id=repo_id,
            filename=mirror.MANIFEST_FILENAME,
            repo_type="dataset",
            revision=remote_oid or "main",
        )
    except EntryNotFoundError:
        return remote_oid, mirror.empty_manifest()
    raw = json.loads(Path(str(local_manifest)).read_text(encoding="utf-8"))
    return remote_oid, mirror.validate_manifest(raw)


def _select_production_files(
    root: Path, source: str, years: set[int] | None
) -> list[tuple[str, Path]]:
    source_dir = root / "production" / f"source={source}"
    if not source_dir.exists():
        return []
    selected: list[tuple[str, Path]] = []
    for year_dir in sorted(source_dir.glob("year=*")):
        year_str = year_dir.name.split("=", 1)[1]
        if not year_str.isdigit():
            continue
        year_int = int(year_str)
        if years is not None and year_int not in years:
            continue
        data_file = year_dir / "data.parquet"
        if data_file.is_file():
            selected.append((year_str, data_file))
    selected.sort(key=lambda pair: int(pair[0]))
    return selected


def plan_publish(
    *,
    store_root: Path,
    repo_id: str,
    remote_oid: str | None,
    remote_manifest: dict[str, Any],
    sources: Sequence[str] | None = None,
    years: Sequence[int] | None = None,
    updated_at: str | None = None,
) -> PublishPlan:
    """Inspect local files, skip unchanged checksums, and evaluate the sanity gate."""
    target_sources = list(dict.fromkeys(sources)) if sources else discover_sources(store_root)
    year_filter = set(years) if years else None
    now = updated_at or mirror.utc_now_iso()

    changes: list[PlannedChange] = []
    skipped = 0
    remote_sources: dict[str, Any] = remote_manifest.get("sources", {})

    for source in target_sources:
        remote_source = remote_sources.get(source, {})
        remote_prod: dict[str, Any] = remote_source.get("production", {})

        for year_str, path in _select_production_files(store_root, source, year_filter):
            new_entry = mirror.production_partition_entry(store_root, path, updated_at=now)
            old_entry = remote_prod.get(year_str)
            if old_entry is not None and old_entry.get("sha256") == new_entry["sha256"]:
                skipped += 1
                continue
            violations = mirror.check_production_entry(source, year_str, new_entry, old_entry)
            changes.append(
                PlannedChange(
                    source=source,
                    kind="production",
                    target=f"year={year_str}",
                    year=year_str,
                    local_path=path,
                    new_entry=new_entry,
                    old_entry=old_entry,
                    violations=tuple(violations),
                )
            )

        entities_path = store_root / "entities" / f"{source}.parquet"
        if entities_path.is_file():
            new_ent = mirror.entities_entry(store_root, entities_path, updated_at=now)
            old_ent = remote_source.get("entities")
            if old_ent is not None and old_ent.get("sha256") == new_ent["sha256"]:
                skipped += 1
            else:
                violations = mirror.check_entities_entry(source, new_ent, old_ent)
                changes.append(
                    PlannedChange(
                        source=source,
                        kind="entities",
                        target="entities",
                        year=None,
                        local_path=entities_path,
                        new_entry=new_ent,
                        old_entry=old_ent,
                        violations=tuple(violations),
                    )
                )

    return PublishPlan(
        repo_id=repo_id,
        remote_oid=remote_oid,
        remote_manifest=remote_manifest,
        changes=tuple(changes),
        skipped=skipped,
    )


def format_diff_table(plan: PublishPlan, *, force: bool = False) -> str:
    """Format a concise human-readable diff table for CI logs and dry runs."""
    if not plan.changes:
        return f"No changed files ({plan.skipped} unchanged file(s) skipped)."

    lines = [
        f"Mirror publish plan for {plan.repo_id} "
        f"({len(plan.changes)} changed, {plan.skipped} unchanged):",
        f"{'source':<12} {'target':<10} {'rows (old -> new)':<26} {'gate':<8} details",
        "-" * 88,
    ]
    for change in plan.changes:
        old_rows = "-" if change.old_entry is None else f"{int(change.old_entry['rows']):,}"
        new_rows = f"{int(change.new_entry['rows']):,}"
        rows_col = f"{old_rows} -> {new_rows}"
        gate = ("FORCED" if force else "FAIL") if change.violations else "OK"

        details_parts: list[str] = []
        if change.kind == "production":
            old_totals = (change.old_entry or {}).get("totals_m3") or {}
            new_totals = change.new_entry.get("totals_m3") or {}
            products = sorted(set(old_totals) | set(new_totals))
            for prod in products:
                old_v = old_totals.get(prod)
                new_v = new_totals.get(prod, 0.0)
                if old_v is None:
                    details_parts.append(f"{prod}: +{new_v:,.0f}m3")
                else:
                    details_parts.append(f"{prod}: {old_v:,.0f}->{new_v:,.0f}m3")
        if change.violations:
            details_parts.append(" | ".join(v.reason for v in change.violations))
        details = "; ".join(details_parts)
        lines.append(f"{change.source:<12} {change.target:<10} {rows_col:<26} {gate:<8} {details}")
    return "\n".join(lines)


def write_github_output(changed: bool, output_path: str | Path | None = None) -> None:
    """Append ``changed=true|false`` to ``$GITHUB_OUTPUT`` when running in GitHub Actions."""
    target = output_path if output_path is not None else os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with Path(target).open("a", encoding="utf-8") as fh:
        fh.write(f"changed={'true' if changed else 'false'}\n")


def _build_updates(changes: Sequence[PlannedChange]) -> dict[str, dict[str, Any]]:
    updates: dict[str, dict[str, Any]] = {}
    for change in changes:
        src = updates.setdefault(change.source, {})
        if change.kind == "production" and change.year is not None:
            src.setdefault("production", {})[change.year] = change.new_entry
        elif change.kind == "entities":
            src["entities"] = change.new_entry
    return updates


def _commit_message(changes: Sequence[PlannedChange]) -> str:
    sources = sorted({c.source for c in changes})
    return f"Update mirror: {', '.join(sources)} ({len(changes)} file(s))"


def publish_mirror(
    *,
    store_root: str | Path | None = None,
    sources: Sequence[str] | None = None,
    years: Sequence[int] | None = None,
    repo: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    tag: str | None = None,
    api: Any | None = None,
    token: str | None = None,
    updated_at: str | None = None,
) -> PublishResult:
    """Inspect local store, run sanity gate, merge manifest, and push atomically to HF."""
    root = store.resolve_root(store_root)
    repo_id = mirror.resolve_repo_id(repo)
    now = updated_at or mirror.utc_now_iso()

    if api is None:
        resolved_token = token if token is not None else os.environ.get("HF_TOKEN")
        if not dry_run and not resolved_token:
            raise RuntimeError(
                "HF_TOKEN environment variable is required to publish (or pass --dry-run)"
            )
        api = HfApi(token=resolved_token or None)

    remote_oid, remote_manifest = fetch_remote_state(api, repo_id)
    plan = plan_publish(
        store_root=root,
        repo_id=repo_id,
        remote_oid=remote_oid,
        remote_manifest=remote_manifest,
        sources=sources,
        years=years,
        updated_at=now,
    )
    print(format_diff_table(plan, force=force))

    if plan.violations and not force:
        raise SanityGateError(plan.violations)

    if not plan.has_changes:
        write_github_output(False)
        return PublishResult(
            repo_id=repo_id,
            changed=False,
            dry_run=dry_run,
            commit_oid=None,
            tag=None,
            changes=plan.changes,
            skipped=plan.skipped,
            merged_manifest=remote_manifest,
        )

    merged = mirror.merge_manifest(remote_manifest, _build_updates(plan.changes), updated_at=now)
    if dry_run:
        print("Dry run: skipping Hugging Face commit.")
        write_github_output(False)
        return PublishResult(
            repo_id=repo_id,
            changed=False,
            dry_run=True,
            commit_oid=None,
            tag=None,
            changes=plan.changes,
            skipped=plan.skipped,
            merged_manifest=merged,
        )

    manifest_bytes = (json.dumps(merged, indent=2) + "\n").encode("utf-8")
    operations: list[Any] = [
        CommitOperationAdd(
            path_in_repo=change.new_entry["path"],
            path_or_fileobj=change.local_path,
        )
        for change in plan.changes
    ]
    operations.append(
        CommitOperationAdd(
            path_in_repo=mirror.MANIFEST_FILENAME,
            path_or_fileobj=manifest_bytes,
        )
    )

    commit_info = api.create_commit(
        repo_id=repo_id,
        operations=operations,
        commit_message=_commit_message(plan.changes),
        repo_type="dataset",
        parent_commit=remote_oid,
    )
    commit_oid: str | None = getattr(commit_info, "oid", None)

    applied_tag: str | None = None
    if tag:
        api.create_tag(
            repo_id=repo_id,
            tag=tag,
            revision=commit_oid or "main",
            repo_type="dataset",
        )
        applied_tag = tag

    write_github_output(True)
    return PublishResult(
        repo_id=repo_id,
        changed=True,
        dry_run=False,
        commit_oid=commit_oid,
        tag=applied_tag,
        changes=plan.changes,
        skipped=plan.skipped,
        merged_manifest=merged,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="publish_mirror.py",
        description="Publish canonical Parquet partitions and entities to a Hugging Face mirror.",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        dest="store_root",
        help="local store root (default: $OGPROD_STORE, else ~/.local/share/ogprod/store)",
    )
    parser.add_argument(
        "--source",
        action="extend",
        nargs="+",
        dest="sources",
        help="source(s) to inspect (repeatable; default: all sources in store)",
    )
    parser.add_argument(
        "--year",
        type=int,
        action="extend",
        nargs="+",
        dest="years",
        help="optional production year filter (repeatable; default: all years)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Hugging Face dataset repo id (default: $OGPROD_MIRROR or mralbu/ogprod)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="bypass sanity-gate drop/empty thresholds",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print planned diff and sanity-gate results without pushing",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help="optional git tag to create on the new Hugging Face commit (e.g. 2026-11)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        publish_mirror(
            store_root=args.store_root,
            sources=args.sources,
            years=args.years,
            repo=args.repo,
            force=args.force,
            dry_run=args.dry_run,
            tag=args.tag,
        )
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
