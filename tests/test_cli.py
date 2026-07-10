from pathlib import Path

import pytest

import ogprod
from ogprod.__main__ import main


def test_fetch_prints_downloaded_paths(monkeypatch, capsys):
    calls = {}

    def fake_fetch(source, dest_dir, **kwargs):
        calls.update(source=source, dest_dir=dest_dir, kwargs=kwargs)
        return [Path("data/a.csv"), Path("data/b.csv")]

    monkeypatch.setattr(ogprod, "fetch", fake_fetch)
    code = main(["fetch", "anp", "--year", "2023", "--month", "1"])

    assert code == 0
    assert calls == {
        "source": "anp",
        "dest_dir": Path("data"),
        "kwargs": {"year": 2023, "month": 1},
    }
    assert capsys.readouterr().out.splitlines() == ["data/a.csv", "data/b.csv"]


def test_fetch_omits_unset_flags_and_honors_dest(monkeypatch):
    calls = {}

    def fake_fetch(source, dest_dir, **kwargs):
        calls.update(source=source, dest_dir=dest_dir, kwargs=kwargs)
        return []

    monkeypatch.setattr(ogprod, "fetch", fake_fetch)
    assert main(["fetch", "sodir", "--dest", "raw"]) == 0
    assert calls == {"source": "sodir", "dest_dir": Path("raw"), "kwargs": {}}


def test_fetch_error_is_reported_not_raised(monkeypatch, capsys):
    def fake_fetch(source, dest_dir, **kwargs):
        raise ValueError("data for 2025 is only queryable in the CDP application")

    monkeypatch.setattr(ogprod, "fetch", fake_fetch)
    code = main(["fetch", "anp", "--year", "2025"])

    assert code == 1
    assert "CDP" in capsys.readouterr().err


def test_unknown_source_rejected_by_parser():
    with pytest.raises(SystemExit):
        main(["fetch", "opec"])
