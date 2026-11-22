"""Scheduled & manual CI driver for refreshing the Hugging Face data mirror.

Applies the per-source rolling refresh policy (Sodir full, Argentina current +
previous year, ANP skipped on schedule unless explicitly requested), isolates
failures per source so a broken regulator or sanity-gate violation never blocks
healthy sources from publishing, and exits non-zero at the end if any source
failed so GitHub alerts the maintainer.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Allow importing sibling scripts.publish_mirror when run directly via
# `python scripts/ci_refresh.py` as well as from pytest / ty.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from huggingface_hub import HfApi  # noqa: E402
from scripts import publish_mirror  # noqa: E402

import ogprod  # noqa: E402
from ogprod import mirror  # noqa: E402


@dataclass(frozen=True)
class SourcePolicy:
    """Rolling refresh policy for one source."""

    #: Whether this source runs on the weekly scheduled workflow.
    scheduled: bool
    #: ``None`` = full source ingest; ``N`` = rolling ``[year, ..., year - N + 1]``.
    rolling_years: int | None


REFRESH_POLICY: dict[str, SourcePolicy] = {
    "sodir": SourcePolicy(scheduled=True, rolling_years=None),
    "argentina": SourcePolicy(scheduled=True, rolling_years=2),
    # Bulk archive freezes at 2023; updated manually or via workflow_dispatch.
    "anp": SourcePolicy(scheduled=False, rolling_years=None),
}


def scheduled_sources() -> list[str]:
    """Sources enabled on the weekly schedule."""
    return [name for name, policy in REFRESH_POLICY.items() if policy.scheduled]


def years_for_source(
    source: str,
    *,
    current_year: int,
    override_years: Sequence[int] | None = None,
) -> list[int] | None:
    """Resolve the list of years to ingest for ``source``, or ``None`` for full ingest."""
    if override_years:
        return list(override_years)
    policy = REFRESH_POLICY.get(source)
    if policy is None or policy.rolling_years is None:
        return None
    return [current_year - offset for offset in range(policy.rolling_years)]


def _parse_csv_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_years(raw: str | None) -> list[int] | None:
    items = _parse_csv_list(raw)
    if not items:
        return None
    return [int(item) for item in items]


def _ingest_source(
    db: ogprod.Store,
    source: str,
    years: Sequence[int] | None,
) -> None:
    if years is None:
        result = db.ingest(source, origin="publisher", refresh=True)
        print(f"ingested {result}")
        return
    for index, year in enumerate(years):
        # Fetch entities once on the first year of the rolling window.
        result = db.ingest(
            source,
            origin="publisher",
            year=year,
            entities=(index == 0),
            refresh=(index == 0),
        )
        print(f"ingested {source} ({year}): {result}")


def run_refresh(
    *,
    sources: Sequence[str] | None = None,
    years: Sequence[int] | None = None,
    store_root: str | Path | None = None,
    repo: str | None = None,
    force: bool = False,
    dry_run: bool = False,
    tag: str | None = None,
    current_year: int | None = None,
    api: Any | None = None,
    token: str | None = None,
) -> int:
    """Run fault-isolated per-source ingest + sanity-gated publish. Returns exit code."""
    target_sources = list(sources) if sources else scheduled_sources()
    available = set(ogprod.available())
    unknown = [src for src in target_sources if src not in available]
    if unknown:
        print(
            f"error: unknown source(s): {unknown}; available: {sorted(available)}",
            file=sys.stderr,
        )
        return 1

    now_year = current_year if current_year is not None else datetime.now(UTC).year
    repo_id = mirror.resolve_repo_id(repo)

    if api is None:
        resolved_token = token if token is not None else os.environ.get("HF_TOKEN")
        if not dry_run and not resolved_token:
            print(
                "error: HF_TOKEN environment variable is required to publish (or pass --dry-run)",
                file=sys.stderr,
            )
            return 1
        api = HfApi(token=resolved_token or None)

    failures: dict[str, str] = {}
    ingested_sources: list[str] = []

    with tempfile.TemporaryDirectory(prefix="ogprod-ci-refresh-") as tmp:
        root = Path(store_root) if store_root is not None else Path(tmp) / "store"
        db = ogprod.Store(root)

        for source in target_sources:
            src_years = years_for_source(source, current_year=now_year, override_years=years)
            print(f"=== Refreshing {source} (years={src_years or 'all'}) ===")
            try:
                _ingest_source(db, source, src_years)
            except Exception as error:
                failures[source] = f"ingest failed: {error}"
                print(f"ERROR: {source} ingest failed: {error}", file=sys.stderr)
                traceback.print_exc(file=sys.stderr)
                continue
            ingested_sources.append(source)

        # Evaluate sanity gate per source so a failing source never blocks healthy ones.
        healthy_sources: list[str] = []
        if ingested_sources:
            remote_oid, remote_manifest = publish_mirror.fetch_remote_state(api, repo_id)
            for source in ingested_sources:
                plan = publish_mirror.plan_publish(
                    store_root=root,
                    repo_id=repo_id,
                    remote_oid=remote_oid,
                    remote_manifest=remote_manifest,
                    sources=[source],
                    years=years,
                )
                if plan.violations and not force:
                    print(publish_mirror.format_diff_table(plan, force=False), file=sys.stderr)
                    reasons = "; ".join(str(v) for v in plan.violations)
                    failures[source] = f"sanity gate failed: {reasons}"
                    print(f"ERROR: {source} failed sanity gate: {reasons}", file=sys.stderr)
                    continue
                healthy_sources.append(source)

        if healthy_sources:
            publish_mirror.publish_mirror(
                store_root=root,
                sources=healthy_sources,
                years=years,
                repo=repo_id,
                force=force,
                dry_run=dry_run,
                tag=tag,
                api=api,
            )
        else:
            publish_mirror.write_github_output(False)

    if failures:
        print("\nRefresh completed with failures:", file=sys.stderr)
        for source, reason in failures.items():
            print(f"  - {source}: {reason}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ci_refresh.py",
        description="Fault-isolated rolling refresh + mirror publish driver.",
    )
    parser.add_argument(
        "--sources",
        default=None,
        help="comma-separated sources (default: scheduled active sources)",
    )
    parser.add_argument(
        "--years",
        default=None,
        help="optional comma-separated years override for backfills",
    )
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        dest="store_root",
        help="optional persistent store root (default: ephemeral temp directory)",
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Hugging Face dataset repo id (default: $OGPROD_MIRROR or mralbu/ogprod)",
    )
    parser.add_argument("--force", action="store_true", help="bypass publish sanity gate")
    parser.add_argument("--dry-run", action="store_true", help="do not push to Hugging Face")
    parser.add_argument("--tag", default=None, help="optional git tag to create on HF after commit")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_refresh(
        sources=_parse_csv_list(args.sources) or None,
        years=_parse_years(args.years),
        store_root=args.store_root,
        repo=args.repo,
        force=args.force,
        dry_run=args.dry_run,
        tag=args.tag or None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
