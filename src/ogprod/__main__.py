"""Command-line entry point.

``python -m ogprod fetch <source>``   — download raw files, print their paths.
``python -m ogprod ingest <source>``  — fetch + parse + upsert into the store.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ogprod
from ogprod import store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogprod", description="Organize public oil & gas production data."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    fetch = commands.add_parser(
        "fetch",
        help="download a source's raw files",
        description="Download a source's raw production files and print their paths.",
        epilog=(
            "Per-source options: sodir takes none (whole history); "
            "argentina takes --year; anp takes --year (plus --month for 2021-2023) "
            "or no flags to download the whole 2005-2023 archive."
        ),
    )
    ingest = commands.add_parser(
        "ingest",
        help="fetch, parse and upsert a source into the local store",
        description=(
            "Download a source's raw files, parse them, and upsert the result "
            "into the Parquet store. Prints the store files written."
        ),
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


def _ingest(args: argparse.Namespace, options: dict) -> list[Path]:
    paths = _fetch(args, options)
    written: list[Path] = []
    if args.entities:
        for path in paths:
            written += store.write_entities(
                ogprod.load_entities(args.source, path), root=args.store_root
            )
    else:
        # One write per raw file, in fetch order: later files upsert over
        # earlier ones (ANP's pré-sal file restates a subset of the mar file).
        for path in paths:
            written += store.write_production(ogprod.load(args.source, path), root=args.store_root)
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
