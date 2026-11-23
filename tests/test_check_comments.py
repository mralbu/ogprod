from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from scripts import check_comments as cc


def test_python_comments_skip_directives_and_strings() -> None:
    source = (
        "#!/usr/bin/env python\n"
        "x = '# not a comment'\n"
        "# own-line comment\n"
        "y = 1  # inline comment\n"
        "z = f()  # noqa: S603\n"
        "w = g()  # type: ignore[arg-type]\n"
        "raise AssertionError  # pragma: no cover\n"
    )
    assert list(cc.python_comments(source)) == [
        (3, "# own-line comment"),
        (4, "# inline comment"),
    ]


def test_ts_comments_skip_directives_jsdoc_and_strings() -> None:
    source = (
        '/// <reference types="vite/client" />\n'
        "/** JSDoc stays. */\n"
        'const url = "https://example.com"; // trailing\n'
        "const t = `pmtiles://${x}`;\n"
        "/* block\n   comment */\n"
        "const s = '/* nope */';\n"
        "// @ts-expect-error\n"
    )
    assert list(cc.ts_comments(source)) == [(3, "// trailing"), (5, "/* block")]


def test_ratchet_flags_growth_and_slack() -> None:
    baseline = {"a.py": 2, "b.py": 1}
    counts = {"a.py": 3, "c.py": 1}
    assert cc.regressions(counts, baseline) == {"a.py": 3, "c.py": 1}
    assert cc.slack(counts, baseline) == {"b.py": 0}


@given(st.dictionaries(st.sampled_from(["a.py", "b.ts", "c.tsx"]), st.integers(0, 5)))
def test_counts_are_their_own_baseline(counts: dict[str, int]) -> None:
    assert cc.regressions(counts, counts) == {}
    assert cc.slack(counts, counts) == {}


@given(st.lists(st.text(alphabet="abc xyz=1\n", max_size=20), max_size=5))
def test_python_scanner_finds_each_added_comment(lines: list[str]) -> None:
    code = "\n".join(f"v{i} = {i}" for i in range(len(lines)))
    assert list(cc.python_comments(code)) == []
    flat = [t.replace("\n", " ") for t in lines]
    commented = "\n".join(f"v{i} = {i}  # {text}" for i, text in enumerate(flat))
    assert len(list(cc.python_comments(commented))) == len(lines)
