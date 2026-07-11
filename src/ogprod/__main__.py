"""Command-line entry point.

``python -m ogprod fetch <source>``   — download raw files, print their paths.
``python -m ogprod ingest <source>``  — fetch + parse + upsert into the store.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import cast

import geopandas as gpd
import pandas as pd

import ogprod
from ogprod import store

#: Flush accumulated parsed rows to the store once a batch reaches this
#: size — bounds memory on whole-archive ingests while rewriting each
#: (source, year) partition far less than once per raw file.
_BATCH_ROWS = 2_000_000

_PER_SOURCE_OPTIONS = (
    "Per-source options: sodir takes none (whole history); "
    "argentina takes --year; anp takes --year (plus --month for 2021-2023) "
    "or no flags to download the whole 2005-2023 archive."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogprod", description="Organize public oil & gas production data."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser(
        "fetch",
        help="download a source's raw files",
        description="Download a source's raw production files and print their paths.",
        epilog=_PER_SOURCE_OPTIONS,
    )
    ingest = commands.add_parser(
        "ingest",
        help="fetch, parse and upsert a source into the local store",
        description=(
            "Download a source's raw files, parse them, and upsert the result "
            "into the Parquet store. Prints the store files written."
        ),
        epilog=_PER_SOURCE_OPTIONS,
    )
    for sub in (fetch, ingest):
        sub.add_argument("source", choices=ogprod.available())
        sub.add_argument("--year", type=int, help="production year (anp, argentina)")
        sub.add_argument("--month", type=int, help="production month (anp monthly files)")
        sub.add_argument(
            "--entities",
            action="store_true",
            help="operate on entity geometry (well coordinates / field ring fences)",
        )
    fetch.add_argument(
        "--dest", type=Path, default=Path("data"), help="download directory (default: data/)"
    )
    ingest.add_argument(
        "--dest",
        type=Path,
        default=Path("data/raw"),
        help="download directory (default: data/raw/)",
    )
    ingest.add_argument(
        "--store",
        type=Path,
        default=None,
        dest="store_root",
        help="store root (default: data/store/ or $OGPROD_STORE)",
    )
    return parser


def _fetch(args: argparse.Namespace, options: dict) -> list[Path]:
    if args.entities:
        return ogprod.fetch_entities(args.source, args.dest, **options)
    return ogprod.fetch(args.source, args.dest, **options)


def _parse(args: argparse.Namespace, path: Path) -> pd.DataFrame:
    load = ogprod.load_entities if args.entities else ogprod.load
    try:
        return load(args.source, path)
    except ValueError as error:
        raise ValueError(f"{error} (while parsing {path})") from error


def _ingest(args: argparse.Namespace, options: dict) -> list[Path]:
    paths = _fetch(args, options)
    if args.entities:
        frames = [_parse(args, path) for path in paths]
        merged = frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)
        return store.write_entities(cast(gpd.GeoDataFrame, merged), root=args.store_root)

    # Parse in fetch order and write in large batches: write_production's
    # keep-last dedup resolves overlapping files (ANP's pré-sal file restates
    # a subset of the mar file), and each (source, year) partition is
    # rewritten once per batch instead of once per raw file.
    written: list[Path] = []
    batch: list[pd.DataFrame] = []
    rows = 0
    try:
        for path in paths:
            frame = _parse(args, path)
            batch.append(frame)
            rows += len(frame)
            if rows >= _BATCH_ROWS:
                written += store.write_production(
                    pd.concat(batch, ignore_index=True), root=args.store_root
                )
                batch, rows = [], 0
        if batch:
            written += store.write_production(
                pd.concat(batch, ignore_index=True), root=args.store_root
            )
    except (ValueError, TypeError):
        # A failure mid-run: report what earlier flushes already wrote (the
        # store is upsert-idempotent, so re-running heals it), then let main
        # print the error.
        for path in dict.fromkeys(written):
            print(path)
        raise
    return written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = {key: value for key in ("year", "month") if (value := getattr(args, key)) is not None}
    try:
        paths = _fetch(args, options) if args.command == "fetch" else _ingest(args, options)
    except (ValueError, TypeError, NotImplementedError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in dict.fromkeys(paths):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
