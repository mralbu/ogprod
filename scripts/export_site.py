"""Thin CLI entrypoint for exporting Layer-2 static site assets (``site/public/data``)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ogprod.export_web import export_web


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export ogprod Layer-2 static web assets.")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"))
    parser.add_argument("--out-dir", type=Path, default=Path("site/public/data"))
    parser.add_argument("--sync-mirror", action="store_true")
    parser.add_argument("--mirror", default=None)
    parser.add_argument("--revision", default="main")
    parser.add_argument("--skip-tiles", action="store_true")
    parser.add_argument("--site-url", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = export_web(
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        sync_mirror=args.sync_mirror,
        mirror_repo=args.mirror,
        revision=args.revision,
        skip_tiles=args.skip_tiles,
        site_url=args.site_url,
    )
    print(
        f"exported {summary.out_dir}: sources={list(summary.sources)} "
        f"search={summary.search_entries:,} tiles={summary.has_tiles}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
