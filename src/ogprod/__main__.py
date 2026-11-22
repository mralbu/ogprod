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
    "Mirror origin (default) accepts --year (one or more years) and --revision. "
    "Publisher origin (--origin publisher): sodir takes no period flags; "
    "argentina takes --year; anp takes --year (plus --month for 2021-2023) "
    "or no flags for the whole 2005-2023 archive."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ogprod", description="Organize public oil & gas production data."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    store_help = "store root (default: $OGPROD_STORE, else ~/.local/share/ogprod/store)"

    ingest = commands.add_parser(
        "ingest",
        help="sync from the mirror or fetch, parse and upsert a source into the local store",
        description=(
            "Pull prebuilt Parquet from the mirror (default, --origin mirror) or "
            "download raw files from the regulator (--origin publisher) or local "
            "--files, parse them, and upsert into the store."
        ),
        epilog=_PER_SOURCE_OPTIONS,
    )
    ingest.add_argument("source", choices=sources.available())
    ingest.add_argument(
        "--origin",
        choices=("mirror", "publisher"),
        default="mirror",
        help="where to pull data from (default: mirror)",
    )
    ingest.add_argument(
        "--revision",
        default="main",
        help="mirror git revision or tag (default: main)",
    )
    ingest.add_argument(
        "--mirror",
        default=None,
        help="mirror Hugging Face repo id or base URL (default: $OGPROD_MIRROR or mralbu/ogprod)",
    )
    ingest.add_argument(
        "--year",
        type=int,
        nargs="+",
        action="extend",
        help="production year(s) (pass one or more for mirror; single year for publisher)",
    )
    ingest.add_argument(
        "--month", type=int, help="production month (publisher origin only, e.g. anp monthly files)"
    )
    ingest.add_argument(
        "--files", type=Path, nargs="+", help="ingest these downloaded files instead of fetching"
    )
    ingest.add_argument(
        "--no-entities", action="store_true", help="skip entity geometry (wells / ring fences)"
    )
    ingest.add_argument(
        "--refresh",
        action="store_true",
        help="force re-download even if already stored / checksum matches",
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
    options: dict = {}
    for key in ("year", "month"):
        value = getattr(args, key)
        if value is None:
            continue
        if key == "year" and isinstance(value, list) and len(value) == 1:
            value = value[0]
        options[key] = value
    return options


def _run(args: argparse.Namespace) -> None:
    if args.command == "ingest":
        result = Store(args.store_root).ingest(
            args.source,
            origin=args.origin,
            revision=args.revision,
            mirror=args.mirror,
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
    except (ValueError, TypeError, NotImplementedError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        for note in getattr(error, "__notes__", []):
            print(note, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
