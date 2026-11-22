"""Command-line interface."""

from importlib.resources import files
from pathlib import Path

import click
from click.shell_completion import get_completion_class

from .core import greet

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


@cli.command()
@click.argument("name", default="world")
def hello(name: str) -> None:
    """Greet NAME (an example command)."""
    click.echo(greet(name))


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
