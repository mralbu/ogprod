"""Command-line entry point: ``python -m ogprod fetch <source> [options]``.

A thin wrapper over :func:`ogprod.fetch` for downloading raw files locally;
parsing and analysis stay in Python (see the README quickstart).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import ogprod


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
    fetch.add_argument("source", choices=ogprod.available())
    fetch.add_argument(
        "--dest", type=Path, default=Path("data"), help="download directory (default: data/)"
    )
    fetch.add_argument("--year", type=int, help="production year (anp, argentina)")
    fetch.add_argument("--month", type=int, help="production month (anp monthly files)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = {key: value for key in ("year", "month") if (value := getattr(args, key)) is not None}
    try:
        paths = ogprod.fetch(args.source, args.dest, **options)
    except (ValueError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
