from pathlib import Path

import pytest

import ogprod
from ogprod import store
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


def test_ingest_fetches_parses_and_stores(tmp_path, monkeypatch, capsys, data_dir):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"

    def fake_fetch(name, dest_dir, **kwargs):
        assert name == "sodir"
        return [sample]

    monkeypatch.setattr(ogprod, "fetch", fake_fetch)
    exit_code = main(
        ["ingest", "sodir", "--dest", str(tmp_path / "raw"), "--store", str(tmp_path / "store")]
    )
    assert exit_code == 0
    partition = tmp_path / "store/production/source=sodir/year=2024/data.parquet"
    assert partition.exists()
    assert str(partition) in capsys.readouterr().out
    loaded = store.load_production(source="sodir", root=tmp_path / "store")
    assert len(loaded) == 10


def test_ingest_entities_flag(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Polygon

    entities = gpd.GeoDataFrame(
        {
            "source": ["sodir"],
            "entity_type": ["field"],
            "entity_id": ["1"],
            "entity_name": ["X"],
            "field": ["X"],
            "basin": [None],
            "region": [None],
            "operator": [None],
            "status": [None],
            "geometry": [Polygon([(0, 0), (1, 0), (1, 1)])],
        },
        crs="EPSG:4326",
    )
    monkeypatch.setattr(ogprod, "fetch_entities", lambda name, dest, **kw: ["raw.zip"])
    monkeypatch.setattr(ogprod, "load_entities", lambda name, raw: entities)
    exit_code = main(
        ["ingest", "sodir", "--entities", "--dest", str(tmp_path), "--store", str(tmp_path / "s")]
    )
    assert exit_code == 0
    assert (tmp_path / "s/entities/sodir.parquet").exists()


def test_ingest_reports_fetch_errors(tmp_path, monkeypatch, capsys):
    def boom(name, dest_dir, **kwargs):
        raise ValueError("no bulk file")

    monkeypatch.setattr(ogprod, "fetch", boom)
    assert main(["ingest", "anp", "--store", str(tmp_path)]) == 1
    assert "no bulk file" in capsys.readouterr().err
