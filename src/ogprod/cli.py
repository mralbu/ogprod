"""Command-line interface."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
from typing import Any

import click
from click.shell_completion import get_completion_class

from ogprod import sources
from ogprod.store import Store

APP_NAME = "ogprod"
COMPLETE_VAR = f"_{APP_NAME.replace('-', '_').upper()}_COMPLETE"
SHELLS = ["bash", "zsh", "fish"]
SKILLS_DIR = Path.home() / ".claude" / "skills"


def skill_text() -> str:
    """The agent skill shipped with the package (`SKILL.md`)."""
    return files("ogprod").joinpath("SKILL.md").read_text(encoding="utf-8")


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="ogprod")
def cli() -> None:
    """Public oil & gas production data pipeline and geospatial store."""


@cli.command("sources")
def list_sources() -> None:
    """List registered data source connectors."""
    for name in sources.available():
        connector = sources.get(name)
        click.echo(
            f"{name}\t{connector.country}\t{connector.publisher}\t"
            f"{connector.entity_type.value}\t{connector.docs_url}"
        )


@cli.command()
@click.argument("source")
@click.option(
    "--origin",
    type=click.Choice(["mirror", "publisher"], case_sensitive=False),
    default="mirror",
    show_default=True,
    help="Download pre-built Parquet from the mirror or fetch live from the regulator portal.",
)
@click.option(
    "--mirror",
    default=None,
    help="Override mirror Hugging Face dataset repo or base URL (default: $OGPROD_MIRROR).",
)
@click.option(
    "--revision",
    default="main",
    show_default=True,
    help="Mirror git revision or tag to read from.",
)
@click.option(
    "--year",
    "years",
    type=int,
    multiple=True,
    help="Ingest specific production year(s); repeatable (e.g. --year 2025 --year 2026).",
)
@click.option(
    "--environment",
    type=click.Choice(["onshore", "offshore"], case_sensitive=False),
    default=None,
    help="Filter source files by onshore or offshore (publisher origin only).",
)
@click.option(
    "--recent",
    is_flag=True,
    help="Only fetch the current and previous year (publisher origin only).",
)
@click.option("--refresh", is_flag=True, help="Re-download files and geo layers already ingested.")
@click.option("--no-geo", is_flag=True, help="Skip geospatial layers (fields, facilities, wells).")
@click.option(
    "--keep-raw",
    is_flag=True,
    help="Keep raw downloaded files under <data-dir>/raw/ (publisher origin only).",
)
@click.option(
    "--files",
    "local_files",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    help="Ingest local production file(s) instead of downloading.",
)
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the ogprod data directory (default: $OGPROD_DATA or XDG data dir).",
)
def ingest(
    source: str,
    origin: str,
    mirror: str | None,
    revision: str,
    years: tuple[int, ...],
    environment: str | None,
    recent: bool,
    refresh: bool,
    no_geo: bool,
    keep_raw: bool,
    local_files: tuple[Path, ...],
    data_dir: Path | None,
) -> None:
    """Download, normalize, and store production & geospatial data for SOURCE."""
    store = Store(data_dir)
    options: dict[str, Any] = {}
    if environment is not None:
        options["environment"] = environment
    if recent:
        options["recent"] = True

    origin_val = "publisher" if origin.lower() == "publisher" else "mirror"
    summary = store.ingest(
        source,
        year=list(years) if years else None,
        origin=origin_val,
        mirror=mirror,
        revision=revision,
        refresh=refresh,
        geo_layers=not no_geo,
        keep_raw=keep_raw,
        files=list(local_files) if local_files else None,
        **options,
    )
    geo_desc = ", ".join(f"{k}={v:,}" for k, v in summary.geo_counts.items()) or "unchanged"
    click.echo(
        f"{summary.source}: {summary.production_rows:,} rows "
        f"({summary.files_ingested} file(s) ingested, {summary.files_skipped} skipped, "
        f"{len(summary.partitions)} partition(s)); geo: {geo_desc}"
    )


@cli.command()
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the ogprod data directory.",
)
def coverage(data_dir: Path | None) -> None:
    """Print production and geospatial coverage per source."""
    frame = Store(data_dir).coverage()
    if frame.empty:
        click.echo("Store is empty.")
        return
    click.echo(frame.to_string(index=False))


@cli.command()
@click.option(
    "--source",
    default=None,
    help="Limit audit to a single source (default: all sources in the store).",
)
@click.option(
    "--stale-months",
    type=int,
    default=6,
    show_default=True,
    help="Warn when a source's latest period is older than this many months.",
)
@click.option(
    "--strict",
    is_flag=True,
    help="Exit non-zero on warnings in addition to errors.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON output.",
)
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the ogprod data directory.",
)
def audit(
    source: str | None,
    stale_months: int,
    strict: bool,
    as_json: bool,
    data_dir: Path | None,
) -> None:
    """Audit stored Parquet and GeoParquet tables for data-quality issues."""
    import json

    report = Store(data_dir).audit(source=source, stale_months=stale_months)
    if as_json:
        click.echo(json.dumps(report.to_dict(), indent=2))
    else:
        click.echo(report.format_text())
    if not report.ok or (strict and report.warnings):
        raise SystemExit(1)


@cli.command("sql")
@click.argument("query")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the ogprod data directory.",
)
def run_sql(query: str, data_dir: Path | None) -> None:
    """Run a DuckDB SQL QUERY over production, fields, facilities, and wells."""
    frame = Store(data_dir).sql(query)
    if frame.empty:
        click.echo("(0 rows)")
        return
    click.echo(frame.to_string(index=False))


@cli.command("export-web")
@click.option(
    "--data-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Override the ogprod data directory (Layer-1 store).",
)
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path("data/web"),
    show_default=True,
    help="Destination directory for Layer-2 web assets (meta, rollups, search, PMTiles).",
)
@click.option(
    "--sync-mirror",
    is_flag=True,
    help="Sync all available sources from the Hugging Face mirror before exporting.",
)
@click.option(
    "--mirror",
    default=None,
    help="Mirror repository or base URL recorded in meta.json (default: $OGPROD_MIRROR).",
)
@click.option(
    "--revision",
    default="main",
    show_default=True,
    help="Mirror git revision recorded in meta.json and used by --sync-mirror.",
)
@click.option(
    "--skip-tiles",
    is_flag=True,
    help="Skip tippecanoe PMTiles generation (exports meta, rollups, search index, and llms.txt).",
)
@click.option(
    "--site-url",
    default=None,
    help="Public base URL of the Web Explorer embedded in generated llms.txt links.",
)
def export_web_cmd(
    data_dir: Path | None,
    out_dir: Path,
    sync_mirror: bool,
    mirror: str | None,
    revision: str,
    skip_tiles: bool,
    site_url: str | None = None,
) -> None:
    """Export Layer-2 web visualization artifacts (meta, rollups, search, llms.txt, PMTiles)."""
    from ogprod.export_web import export_web

    summary = export_web(
        data_dir=data_dir,
        out_dir=out_dir,
        sync_mirror=sync_mirror,
        mirror_repo=mirror,
        revision=revision,
        skip_tiles=skip_tiles,
        site_url=site_url,
    )
    src_list = ", ".join(summary.sources) or "(none)"
    tiles_status = "yes" if summary.has_tiles else "no"
    click.echo(
        f"exported web assets to {summary.out_dir}: sources=[{src_list}], "
        f"search={summary.search_entries:,}, tiles={tiles_status}"
    )


@cli.command()
@click.argument("shell", type=click.Choice(SHELLS))
def completion(shell: str) -> None:
    """Print the shell completion script for SHELL.

    \b
    bash:  eval "$(ogprod completion bash)"     # in ~/.bashrc
    zsh:   eval "$(ogprod completion zsh)"      # in ~/.zshrc
    fish:  ogprod completion fish \
             > ~/.config/fish/completions/ogprod.fish
    """
    comp_cls = get_completion_class(shell)
    assert comp_cls is not None  # SHELLS only lists shells that click supports
    click.echo(comp_cls(cli, {}, APP_NAME, COMPLETE_VAR).source())


@cli.command()
@click.option(
    "--install",
    is_flag=True,
    help="Write SKILL.md to <skills-dir>/ogprod/ instead of printing it.",
)
@click.option(
    "--skills-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=SKILLS_DIR,
    show_default="~/.claude/skills",
    help="Skills directory used by --install (e.g. .claude/skills for a project).",
)
def skill(install: bool, skills_dir: Path) -> None:
    """Print the agent skill (SKILL.md) that documents this CLI."""
    text = skill_text()
    if not install:
        click.echo(text, nl=False)
        return
    path = skills_dir / APP_NAME / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    click.echo(f"Installed {path}", err=True)


def main() -> None:
    cli(prog_name=APP_NAME)
