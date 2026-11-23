"""Ratchet the number of code comments per file down to zero (see docs/adr/0001).

``comment-baseline.json`` maps each file to the comments it may still hold. A file may
not gain comments; once it loses some, ``--update`` lowers its allowance.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

BASELINE = Path("comment-baseline.json")
PATTERNS = ("*.py", "site/src/*.ts", "site/src/*.tsx", "site/*.ts")

_PY_DIRECTIVE = re.compile(r"#\s*(noqa\b|type:|ty:|pragma\b|fmt:|pyright:|mypy:|-\*-)|#!")
_TS_DIRECTIVE = re.compile(r"///\s*<reference\b|/\*\*(?!/)|//\s*@ts-|/\*\s*eslint|//\s*eslint")


@dataclass(frozen=True)
class Comment:
    path: str
    line: int
    text: str


def python_comments(source: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line, text)`` for every non-directive comment in Python source."""
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and not _PY_DIRECTIVE.match(token.string):
            yield token.start[0], token.string


def _skip_string(source: str, start: int) -> int:
    quote = source[start]
    i = start + 1
    while i < len(source) and source[i] != quote:
        if source[i] == "\\":
            i += 1
        elif source[i] == "\n" and quote != "`":
            break
        i += 1
    return i + 1


def _comment_end(source: str, start: int) -> int:
    if source.startswith("//", start):
        end = source.find("\n", start)
    else:
        end = source.find("*/", start + 2)
        end = -1 if end == -1 else end + 2
    return len(source) if end == -1 else end


def ts_comments(source: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line, text)`` for every non-directive, non-JSDoc comment in TypeScript."""
    i = 0
    while i < len(source):
        char = source[i]
        if char in "\"'`":
            i = _skip_string(source, i)
        elif source.startswith(("//", "/*"), i):
            end = _comment_end(source, i)
            text = source[i:end]
            if not _TS_DIRECTIVE.match(text):
                yield source.count("\n", 0, i) + 1, text.splitlines()[0]
            i = end
        else:
            i += 1


def file_comments(path: str, source: str) -> list[Comment]:
    """Return the banned comments in one file, chosen by its extension."""
    scan = python_comments if path.endswith(".py") else ts_comments
    return [Comment(path, line, text) for line, text in scan(source)]


def count_by_file(comments: Iterable[Comment]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for comment in comments:
        counts[comment.path] = counts.get(comment.path, 0) + 1
    return dict(sorted(counts.items()))


def regressions(counts: Mapping[str, int], baseline: Mapping[str, int]) -> dict[str, int]:
    """Files holding more comments than the baseline allows, with their counts."""
    return {path: n for path, n in counts.items() if n > baseline.get(path, 0)}


def slack(counts: Mapping[str, int], baseline: Mapping[str, int]) -> dict[str, int]:
    """Files whose allowance can be lowered, with their current counts."""
    return {path: counts.get(path, 0) for path, n in baseline.items() if counts.get(path, 0) < n}


def _tracked_files() -> list[str]:
    out = subprocess.run(  # noqa: S603
        ["git", "ls-files", "--", *PATTERNS],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [p for p in out.splitlines() if Path(p).is_file()]


def _scan(paths: Iterable[str]) -> list[Comment]:
    return [c for p in paths for c in file_comments(p, Path(p).read_text(encoding="utf-8"))]


def _load_baseline() -> dict[str, int]:
    return json.loads(BASELINE.read_text()) if BASELINE.exists() else {}


def _write_baseline(counts: Mapping[str, int]) -> None:
    BASELINE.write_text(json.dumps(dict(counts), indent=2, sort_keys=True) + "\n")


def _report(comments: list[Comment], over: Mapping[str, int], baseline: Mapping[str, int]) -> None:
    for c in comments:
        if c.path in over:
            print(f"{c.path}:{c.line}: comment: {c.text}")
    for path, n in over.items():
        print(f"{path}: {n} comments, {baseline.get(path, 0)} allowed", file=sys.stderr)
    print(
        "Comments are banned (docs/adr/0001): delete it, move a decision to docs/adr/, "
        "or file an issue for a workaround.",
        file=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="lower the baseline to match")
    parser.add_argument("--list", action="store_true", help="print every remaining comment")
    args = parser.parse_args(argv)

    comments = _scan(_tracked_files())
    counts = count_by_file(comments)
    baseline = _load_baseline()
    if args.list:
        for c in comments:
            print(f"{c.path}:{c.line}: {c.text}")
        return 0
    over = regressions(counts, baseline)
    if over:
        _report(comments, over, baseline)
        return 1
    if args.update:
        _write_baseline(counts)
        return 0
    if lower := slack(counts, baseline):
        print(f"Comments removed from {', '.join(lower)}: run `just comments --update`")
        return 1
    print(f"{sum(counts.values())} comments left in {len(counts)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
