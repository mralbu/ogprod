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
        click.echo(f"{name}\t{connector.entity_type.value}\t{connector.docs_url}")


@cli.command()
@click.argument("source")
@click.option("--year", type=int, default=None, help="Ingest a specific production year.")
@click.option(
    "--environment",
    type=click.Choice(["onshore", "offshore"], case_sensitive=False),
    default=None,
    help="Filter source files by onshore or offshore (when supported).",
)
@click.option("--recent", is_flag=True, help="Only fetch the current and previous year.")
@click.option("--refresh", is_flag=True, help="Re-download files and geo layers already ingested.")
@click.option("--no-geo", is_flag=True, help="Skip geospatial layers (fields, facilities, wells).")
@click.option("--keep-raw", is_flag=True, help="Keep raw downloaded files under <data-dir>/raw/.")
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
    year: int | None,
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

    summary = store.ingest(
        source,
        year=year,
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
