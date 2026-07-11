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


def test_ingest_concatenates_files_into_one_write(tmp_path, monkeypatch, data_dir):
    # Per-file writes rewrite the same (source, year) partition once per raw
    # file; parsed frames must be concatenated (in fetch order) and written
    # in one batch, with duplicate keys keeping the last occurrence.
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    monkeypatch.setattr(ogprod, "fetch", lambda name, dest, **kw: [sample, sample])
    calls = []
    real_write = store.write_production

    def spy(frame, root=None):
        calls.append(len(frame))
        return real_write(frame, root=root)

    monkeypatch.setattr(store, "write_production", spy)
    assert main(["ingest", "sodir", "--dest", str(tmp_path), "--store", str(tmp_path / "s")]) == 0
    assert len(calls) == 1
    assert len(store.load_production(root=tmp_path / "s")) == 10  # overlap deduped, not summed


def test_ingest_parse_error_names_failing_file(tmp_path, monkeypatch, capsys, data_dir):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    bad = tmp_path / "bogus.csv"
    bad.write_text("a,b\n1,2\n")
    monkeypatch.setattr(ogprod, "fetch", lambda name, dest, **kw: [sample, bad])
    assert main(["ingest", "sodir", "--dest", str(tmp_path), "--store", str(tmp_path / "s")]) == 1
    assert "bogus.csv" in capsys.readouterr().err
    # The batch never flushed before the failure, so nothing was written.
    assert not (tmp_path / "s/production").exists()


def test_ingest_entities_merges_multiple_files(tmp_path, monkeypatch):
    import geopandas as gpd
    from shapely.geometry import Point

    def entities_for(entity_id):
        return gpd.GeoDataFrame(
            {
                "source": ["sodir"],
                "entity_type": ["well"],
                "entity_id": [entity_id],
                "entity_name": [entity_id],
                "field": [None],
                "basin": [None],
                "region": [None],
                "operator": [None],
                "status": [None],
                "geometry": [Point(3.5, 60.6)],
            },
            crs="EPSG:4326",
        )

    monkeypatch.setattr(ogprod, "fetch_entities", lambda name, dest, **kw: ["one", "two"])
    monkeypatch.setattr(ogprod, "load_entities", lambda name, raw: entities_for(raw))
    exit_code = main(
        ["ingest", "sodir", "--entities", "--dest", str(tmp_path), "--store", str(tmp_path / "s")]
    )
    assert exit_code == 0
    # Whole-file replace per source must not lose the first file's entities.
    assert set(store.load_entities(root=tmp_path / "s")["entity_id"]) == {"one", "two"}


def test_ingest_reports_fetch_errors(tmp_path, monkeypatch, capsys):
    def boom(name, dest_dir, **kwargs):
        raise ValueError("no bulk file")

    monkeypatch.setattr(ogprod, "fetch", boom)
    assert main(["ingest", "anp", "--store", str(tmp_path)]) == 1
    assert "no bulk file" in capsys.readouterr().err
