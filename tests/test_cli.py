"""Tests for the ogprod Click CLI."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from ogprod.cli import APP_NAME, SHELLS, cli, skill_text

DATA_DIR = Path(__file__).parent / "data"


def invoke(*args: str) -> str:
    result = CliRunner().invoke(cli, args, prog_name=APP_NAME)
    assert result.exit_code == 0, result.output
    return result.stdout


def test_help() -> None:
    assert "Usage:" in invoke("--help")


def test_version() -> None:
    assert re.search(r"\d+\.\d+", invoke("--version"))


def test_sources_lists_anp() -> None:
    out = invoke("sources")
    assert "anp\twell\t" in out


def test_ingest_local_file_coverage_and_sql(tmp_path: Path) -> None:
    csv_path = DATA_DIR / "anp_mar_2026_sample.csv"
    data_dir = tmp_path / "data"

    out = invoke(
        "ingest",
        "anp",
        "--files",
        str(csv_path),
        "--no-geo",
        "--data-dir",
        str(data_dir),
    )
    assert "anp: 2 rows" in out

    cov = invoke("coverage", "--data-dir", str(data_dir))
    assert "anp" in cov
    assert "2026-01-01" in cov

    res = invoke(
        "sql",
        "SELECT count(*) AS n FROM production",
        "--data-dir",
        str(data_dir),
    )
    assert "2" in res


@pytest.mark.parametrize("shell", SHELLS)
def test_completion(shell: str) -> None:
    assert APP_NAME in invoke("completion", shell)


def test_skill_prints_skill_md() -> None:
    assert invoke("skill") == skill_text()


def test_skill_frontmatter() -> None:
    match = re.match(r"---\nname: (?P<name>.+)\ndescription: .+?\n---\n", skill_text(), re.DOTALL)
    assert match, "SKILL.md must start with YAML frontmatter (name, description)"
    assert match["name"] == APP_NAME


def test_skill_install(tmp_path: Path) -> None:
    invoke("skill", "--install", "--skills-dir", str(tmp_path))
    assert (tmp_path / APP_NAME / "SKILL.md").read_text(encoding="utf-8") == skill_text()
