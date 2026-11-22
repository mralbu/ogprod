from pathlib import Path

import pytest

import ogprod
from ogprod.__main__ import main
from ogprod.sources import anp


def test_fetch_prints_downloaded_paths(monkeypatch, capsys):
    calls = {}

    def fake_fetch(self, dest_dir, **kwargs):
        calls.update(dest_dir=dest_dir, kwargs=kwargs)
        return [Path("data/a.csv"), Path("data/b.csv")]

    monkeypatch.setattr(anp.ANP, "fetch", fake_fetch)
    code = main(["fetch", "anp", "--year", "2023", "--month", "1"])

    assert code == 0
    assert calls == {"dest_dir": Path("data"), "kwargs": {"year": 2023, "month": 1}}
    assert capsys.readouterr().out.splitlines() == ["data/a.csv", "data/b.csv"]


def test_fetch_entities_honors_dest(monkeypatch, capsys):
    monkeypatch.setattr(anp.ANP, "fetch_entities", lambda self, dest: [Path(dest) / "w.csv"])
    assert main(["fetch", "anp", "--entities", "--dest", "raw"]) == 0
    assert capsys.readouterr().out.splitlines() == ["raw/w.csv"]


def test_fetch_error_is_reported_not_raised(monkeypatch, capsys):
    def fake_fetch(self, dest_dir, **kwargs):
        raise ValueError("data for 2025 is only queryable in the CDP application")

    monkeypatch.setattr(anp.ANP, "fetch", fake_fetch)
    code = main(["fetch", "anp", "--year", "2025"])

    assert code == 1
    assert "CDP" in capsys.readouterr().err


def test_unknown_source_rejected_by_parser():
    with pytest.raises(SystemExit):
        main(["fetch", "opec"])


def test_ingest_files_into_store(tmp_path, capsys, data_dir):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    root = tmp_path / "store"
    code = main(["ingest", "sodir", "--files", str(sample), "--no-entities", "--store", str(root)])

    assert code == 0
    assert "sodir: 10 production rows" in capsys.readouterr().out
    assert len(ogprod.Store(root).production(source="sodir")) == 10


def test_ingest_maps_flags_to_store(monkeypatch):
    calls = {}

    def fake_ingest(self, source, **kwargs):
        calls.update(root=self.root, source=source, **kwargs)
        return "ok"

    monkeypatch.setattr(ogprod.Store, "ingest", fake_ingest)
    assert main(["ingest", "anp", "--year", "2023", "--refresh", "--store", "s"]) == 0
    assert calls == {
        "root": Path("s"),
        "source": "anp",
        "origin": "mirror",
        "revision": "main",
        "mirror": None,
        "files": None,
        "entities": True,
        "refresh": True,
        "year": 2023,
    }


def test_ingest_maps_mirror_and_multi_year_flags(monkeypatch):
    calls = {}

    def fake_ingest(self, source, **kwargs):
        calls.update(root=self.root, source=source, **kwargs)
        return "ok"

    monkeypatch.setattr(ogprod.Store, "ingest", fake_ingest)
    code = main(
        [
            "ingest",
            "anp",
            "--year",
            "2022",
            "--year",
            "2023",
            "--revision",
            "2026-11",
            "--mirror",
            "custom/repo",
            "--store",
            "s",
        ]
    )
    assert code == 0
    assert calls["origin"] == "mirror"
    assert calls["revision"] == "2026-11"
    assert calls["mirror"] == "custom/repo"
    assert calls["year"] == [2022, 2023]

    calls.clear()
    code = main(["ingest", "anp", "--year", "2021", "2022", "--store", "s"])
    assert code == 0
    assert calls["year"] == [2021, 2022]


def test_ingest_publisher_origin_flag(monkeypatch):
    calls = {}

    def fake_ingest(self, source, **kwargs):
        calls.update(source=source, **kwargs)
        return "ok"

    monkeypatch.setattr(ogprod.Store, "ingest", fake_ingest)
    code = main(["ingest", "anp", "--origin", "publisher", "--year", "2023", "--month", "6"])
    assert code == 0
    assert calls["origin"] == "publisher"
    assert calls["year"] == 2023
    assert calls["month"] == 6


def test_ingest_mirror_rejects_month(capsys):
    code = main(["ingest", "anp", "--year", "2023", "--month", "6", "--store", "s"])
    assert code == 1
    assert "origin='publisher'" in capsys.readouterr().err


def test_ingest_errors_are_reported(tmp_path, capsys, data_dir):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    code = main(["ingest", "sodir", "--files", str(sample), "--year", "2024", "--store", "s"])
    assert code == 1
    assert "omit them when passing files=" in capsys.readouterr().err


def test_coverage_prints_table(tmp_path, capsys, data_dir):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    ogprod.Store(tmp_path).ingest("sodir", files=[sample], entities=False)
    assert main(["coverage", "--store", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "producing_entities" in out
    assert "sodir" in out


def test_ingest_failure_lists_partitions_already_written(tmp_path, capsys, data_dir, monkeypatch):
    from ogprod import store

    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    bad = tmp_path / "bogus.csv"
    bad.write_text("a,b\n1,2\n")
    monkeypatch.setattr(store, "_BATCH_ROWS", 1)
    args = ["ingest", "sodir", "--files", str(sample), str(bad), "--no-entities"]
    assert main([*args, "--store", str(tmp_path / "s")]) == 1
    err = capsys.readouterr().err
    assert "bogus.csv" in err
    assert "source=sodir/year=2024/data.parquet" in err
