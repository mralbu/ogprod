"""Scheduled & manual CI refresh orchestrator with per-source fault isolation.

Fetches each requested source live from its regulator portal (``origin="publisher"``).
If one regulator portal fails, healthy sources are still published to the Hugging Face
v2 mirror before the script exits non-zero so GitHub Actions alerts the maintainer.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ogprod import sources, store
from scripts import publish_mirror

#: Default sources refreshed on GitHub Actions Azure runners.
#: Brazil (www.gov.br) resets TCP connections from Azure IPs; seed/update Brazil locally
#: via ``scripts/publish_mirror.py`` or manually trigger when reachable.
DEFAULT_CI_SOURCES: tuple[str, ...] = ("norway", "mexico")


@dataclass(frozen=True)
class RefreshOutcome:
    """Result of refreshing a set of sources and publishing healthy ones."""

    succeeded: tuple[str, ...]
    failed: dict[str, str]
    publish: publish_mirror.PublishResult | None


def resolve_source_list(spec: str | None) -> list[str]:
    """Parse a comma-separated source list (or ``'all'``) and validate against registry."""
    if not spec or not spec.strip():
        return list(DEFAULT_CI_SOURCES)
    raw = spec.strip().lower()
    if raw == "all":
        return sources.available()
    chosen = [part.strip() for part in raw.split(",") if part.strip()]
    known = set(sources.available())
    unknown = [s for s in chosen if s not in known]
    if unknown:
        raise ValueError(f"unknown source(s): {unknown}; available: {sorted(known)}")
    return list(dict.fromkeys(chosen))


def _emit_output(key: str, value: str) -> None:
    out_path = os.environ.get("GITHUB_OUTPUT")
    if not out_path:
        return
    with Path(out_path).open("a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")


def run_refresh(
    selected_sources: list[str],
    *,
    data_dir: str | Path = "./data",
    repo_id: str | None = None,
    recent: bool = True,
    geo_layers: bool = True,
    dry_run: bool = False,
    force: bool = False,
    prune_legacy: bool = False,
    tag: str | None = None,
    db: store.Store | None = None,
    publish_fn: Any = None,
) -> RefreshOutcome:
    """Ingest each source with fault isolation, then publish healthy sources."""
    target_db = db if db is not None else store.Store(data_dir)
    publisher = publish_fn if publish_fn is not None else publish_mirror.publish_store

    succeeded: list[str] = []
    failed: dict[str, str] = {}

    for src in selected_sources:
        try:
            summary = target_db.ingest(
                src,
                origin="publisher",
                refresh=True,
                geo_layers=geo_layers,
                recent=recent,
            )
            print(
                f"[ok] {src}: {summary.production_rows:,} rows, "
                f"{len(summary.partitions)} partition(s), geo={summary.geo_counts}"
            )
            succeeded.append(src)
        except Exception as err:
            failed[src] = f"{type(err).__name__}: {err}"
            print(f"[error] {src} failed: {failed[src]}", file=sys.stderr)

    pub_result: publish_mirror.PublishResult | None = None
    if succeeded:
        pub_result = publisher(
            data_dir=target_db.root,
            repo_id=repo_id,
            selected_sources=succeeded,
            dry_run=dry_run,
            force=force,
            prune_legacy=prune_legacy,
            tag=tag,
        )
    else:
        _emit_output("changed", "false")

    _emit_output("succeeded", ",".join(succeeded))
    _emit_output("failed", ",".join(sorted(failed)))
    return RefreshOutcome(succeeded=tuple(succeeded), failed=failed, publish=pub_result)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fault-isolated CI refresh and mirror publish.")
    parser.add_argument(
        "--sources",
        default=",".join(DEFAULT_CI_SOURCES),
        help="Comma-separated source list or 'all' (default: norway,mexico).",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--repo", default=None, help="HF dataset repo override.")
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="Disable --recent filter and fetch full history for multi-file sources.",
    )
    parser.add_argument("--no-geo", action="store_true", help="Skip geospatial layer refresh.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--prune-legacy", action="store_true")
    parser.add_argument("--tag", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    selected = resolve_source_list(args.sources)
    outcome = run_refresh(
        selected,
        data_dir=args.data_dir,
        repo_id=args.repo,
        recent=not args.full_history,
        geo_layers=not args.no_geo,
        dry_run=args.dry_run,
        force=args.force,
        prune_legacy=args.prune_legacy,
        tag=args.tag,
    )
    if outcome.publish is not None:
        print(
            f"publish {outcome.publish.repo_id}: changed={outcome.publish.changed} "
            f"uploaded={outcome.publish.uploaded} skipped={outcome.publish.skipped} "
            f"deleted={outcome.publish.deleted}"
        )
    if outcome.failed:
        print(f"refresh completed with failures: {outcome.failed}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
