import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from ogprod.cli import APP_NAME, SHELLS, cli, skill_text


def invoke(*args: str) -> str:
    result = CliRunner().invoke(cli, args, prog_name=APP_NAME)
    assert result.exit_code == 0, result.output
    return result.stdout


def test_help() -> None:
    assert "Usage:" in invoke("--help")


def test_version() -> None:
    assert re.search(r"\d+\.\d+", invoke("--version"))


def test_hello() -> None:
    assert invoke("hello", "Ada") == "Hello, Ada!\n"


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
