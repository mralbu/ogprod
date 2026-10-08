"""Command-line entry point — a thin adapter over :class:`ogprod.Store`.

``python -m ogprod ingest <source>``  — fetch + parse + upsert into the store.
``python -m ogprod coverage``         — what the store holds, per source.
``python -m ogprod fetch <source>``   — download raw files only, print their paths.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ogprod import sources
from ogprod.store import Store

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
    store_help = "store root (default: $OGPROD_STORE, else ~/.local/share/ogprod/store)"

    ingest = commands.add_parser(
        "ingest",
        help="fetch, parse and upsert a source into the local store",
        description=(
            "Download a source's raw files (or take --files), parse them, and "
            "upsert the result into the store. Entities are ingested too the "
            "first time, or again with --refresh."
        ),
        epilog=_PER_SOURCE_OPTIONS,
    )
    ingest.add_argument("source", choices=sources.available())
    ingest.add_argument("--year", type=int, help="production year (anp, argentina)")
    ingest.add_argument("--month", type=int, help="production month (anp monthly files)")
    ingest.add_argument(
        "--files", type=Path, nargs="+", help="ingest these downloaded files instead of fetching"
    )
    ingest.add_argument(
        "--no-entities", action="store_true", help="skip entity geometry (wells / ring fences)"
    )
    ingest.add_argument(
        "--refresh", action="store_true", help="re-download entities even if already stored"
    )
    ingest.add_argument("--store", type=Path, default=None, dest="store_root", help=store_help)

    coverage = commands.add_parser("coverage", help="show what the store holds, per source")
    coverage.add_argument("--store", type=Path, default=None, dest="store_root", help=store_help)

    fetch = commands.add_parser(
        "fetch",
        help="download a source's raw files without ingesting them",
        description="Download a source's raw files and print their paths.",
        epilog=_PER_SOURCE_OPTIONS,
    )
    fetch.add_argument("source", choices=sources.available())
    fetch.add_argument("--year", type=int, help="production year (anp, argentina)")
    fetch.add_argument("--month", type=int, help="production month (anp monthly files)")
    fetch.add_argument(
        "--entities", action="store_true", help="download entity geometry files instead"
    )
    fetch.add_argument(
        "--dest", type=Path, default=Path("data"), help="download directory (default: data/)"
    )
    return parser


def _options(args: argparse.Namespace) -> dict:
    return {key: value for key in ("year", "month") if (value := getattr(args, key)) is not None}


def _run(args: argparse.Namespace) -> None:
    if args.command == "ingest":
        result = Store(args.store_root).ingest(
            args.source,
            files=args.files,
            entities=not args.no_entities,
            refresh=args.refresh,
            **_options(args),
        )
        print(result)
    elif args.command == "coverage":
        print(Store(args.store_root).coverage().to_string(index=False))
    else:
        connector = sources.get(args.source)
        fetch = connector.fetch_entities if args.entities else connector.fetch
        for path in fetch(args.dest, **_options(args)):
            print(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _run(args)
    except (ValueError, TypeError, NotImplementedError) as error:
        print(f"error: {error}", file=sys.stderr)
        for note in getattr(error, "__notes__", []):
            print(note, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
