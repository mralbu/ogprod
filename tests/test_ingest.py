"""Tests for :class:`ogprod.Store` — the ingest pipeline and its read side.

HTTP goes through an injected ``httpx2.MockTransport``; nothing touches the
network.
"""

from pathlib import Path

import httpx2
import pandas as pd
import pytest

import ogprod
from ogprod import store
from ogprod.sources import anp, base, get, sodir

FIELD_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"fldName": "TROLL", "fldNpdidField": "46437"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[3.5, 60.6], [3.6, 60.6], [3.6, 60.7], [3.5, 60.6]]],
            },
        }
    ],
}


@pytest.fixture
def requests() -> list[str]:
    return []


@pytest.fixture
def sodir_client(data_dir, requests) -> httpx2.Client:
    csv = (data_dir / "sodir_field_production_monthly_sample.csv").read_bytes()

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.host)
        if request.url.host == "factpages.sodir.no":
            return httpx2.Response(200, content=csv)
        if request.url.host == "factmaps.sodir.no":
            return httpx2.Response(200, json=FIELD_GEOJSON)
        return httpx2.Response(404)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_ingest_fetches_production_and_entities(tmp_path, sodir_client):
    db = ogprod.Store(tmp_path)
    result = db.ingest("sodir", origin="publisher", client=sodir_client)

    assert result.source == "sodir"
    assert result.rows == len(db.production()) == 10
    assert result.first_period == result.last_period == pd.Timestamp("2024-05-01")
    assert result.partitions == (tmp_path / "production/source=sodir/year=2024/data.parquet",)
    assert result.entities == 1
    assert list(db.entities()["entity_id"]) == ["46437"]
    assert "10 production rows 2024-05..2024-05" in str(result)


def test_ingest_skips_stored_entities_unless_refresh(tmp_path, sodir_client, requests):
    db = ogprod.Store(tmp_path)
    db.ingest("sodir", origin="publisher", client=sodir_client)
    requests.clear()

    again = db.ingest("sodir", origin="publisher", client=sodir_client)
    assert again.entities is None
    assert requests == ["factpages.sodir.no"]

    refreshed = db.ingest("sodir", origin="publisher", refresh=True, client=sodir_client)
    assert refreshed.entities == 1
    assert "factmaps.sodir.no" in requests


def test_ingest_files_without_entities_is_offline(tmp_path, data_dir):
    def offline(request):
        raise AssertionError(f"unexpected request to {request.url}")

    client = httpx2.Client(transport=httpx2.MockTransport(offline))
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    result = ogprod.Store(tmp_path).ingest("sodir", files=[sample], entities=False, client=client)
    assert result.rows == 10
    assert result.entities is None


def test_ingest_rejects_fetch_options_with_files(tmp_path, data_dir):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    with pytest.raises(TypeError, match="year"):
        ogprod.Store(tmp_path).ingest("sodir", files=[sample], year=2024)


def test_ingest_rejects_options_the_connector_does_not_take(tmp_path, sodir_client):
    with pytest.raises(TypeError):
        ogprod.Store(tmp_path).ingest("sodir", origin="publisher", year=2024, client=sodir_client)


def _anp_files(tmp_path, data_dir) -> dict[str, Path]:
    """terra, mar and a pré-sal file restating mar's rows with other volumes."""
    sample = (data_dir / "anp_pocos_sample.csv").read_text(encoding="utf-8-sig")
    header, tupi, canto = sample.splitlines()
    files = {
        "2024_01_producao_terra.csv": f"{header}\n{canto}\n",
        "2024_01_producao_mar.csv": f"{header}\n{tupi}\n",
        "2024_01_producao_presal.csv": f"{header}\n{tupi.replace('12345,6', '99,0')}\n",
    }
    paths = {}
    for name, text in files.items():
        paths[name] = tmp_path / name
        paths[name].write_text(text, encoding="utf-8")
    return paths


def test_anp_presal_restates_mar_whatever_order_files_arrive(tmp_path, data_dir):
    (tmp_path / "raw").mkdir()
    paths = _anp_files(tmp_path / "raw", data_dir)
    shuffled = [
        paths["2024_01_producao_presal.csv"],
        paths["2024_01_producao_mar.csv"],
        paths["2024_01_producao_terra.csv"],
    ]
    db = ogprod.Store(tmp_path / "store")
    db.ingest("anp", files=shuffled, entities=False)

    oil = db.production(product="oil").set_index("entity_id")["volume"]
    assert oil.to_dict() == pytest.approx({"7-CAM-99-RN": 85.3, "7-TPI-1-RJS": 99.0})


def test_anp_restatement_order_puts_presal_last():
    order = anp.ANP().restatement_order(
        [
            Path("x/2019_05_producao_presal.csv"),
            Path("x/2019_05_producao_Mar.csv"),
            Path("x/cdp-export.csv"),
            Path("x/2019_05_producao_terra.csv"),
        ]
    )
    assert [p.name for p in order] == [
        "cdp-export.csv",
        "2019_05_producao_terra.csv",
        "2019_05_producao_Mar.csv",
        "2019_05_producao_presal.csv",
    ]


def test_ingest_batches_across_files(tmp_path, data_dir, monkeypatch):
    # Frames are combined per batch: each partition is rewritten once per
    # batch, and a key repeated across files is kept once, not summed.
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    writes = []
    real_write = store.write_production

    def spy(frame, root=None):
        writes.append(len(frame))
        return real_write(frame, root=root)

    monkeypatch.setattr(store, "write_production", spy)
    db = ogprod.Store(tmp_path)
    db.ingest("sodir", files=[sample, sample], entities=False)
    assert writes == [10]

    writes.clear()
    monkeypatch.setattr(store, "_BATCH_ROWS", 10)
    db.ingest("sodir", files=[sample, sample], entities=False)
    assert writes == [10, 10]
    assert len(db.production()) == 10


def test_ingest_parse_error_names_file_and_keeps_flushed_partitions(
    tmp_path, data_dir, monkeypatch
):
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    bad = tmp_path / "bogus.csv"
    bad.write_text("a,b\n1,2\n")
    monkeypatch.setattr(store, "_BATCH_ROWS", 1)  # flush after every file
    db = ogprod.Store(tmp_path / "s")
    with pytest.raises(ValueError, match="bogus.csv") as caught:
        db.ingest("sodir", files=[sample, bad], entities=False)
    assert len(db.production()) == 10
    # The error carries the partitions the earlier flush already wrote.
    (note,) = caught.value.__notes__
    assert str(tmp_path / "s/production/source=sodir/year=2024/data.parquet") in note


def test_entity_failure_notes_production_already_written(tmp_path, data_dir, monkeypatch):
    def broken(self, dest, client=None):
        raise ValueError("cadastre endpoint moved")

    monkeypatch.setattr(sodir.Sodir, "fetch_entities", broken)
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    db = ogprod.Store(tmp_path)
    with pytest.raises(ValueError, match="endpoint moved") as caught:
        db.ingest("sodir", files=[sample])
    assert "year=2024" in caught.value.__notes__[0]
    assert len(db.production()) == 10


def test_empty_entity_download_writes_nothing(tmp_path, data_dir, sodir_client, monkeypatch):
    db = ogprod.Store(tmp_path)
    db.ingest("sodir", origin="publisher", client=sodir_client)  # stores one ring fence
    monkeypatch.setattr(sodir.Sodir, "fetch_entities", lambda self, dest, client=None: [])
    sample = data_dir / "sodir_field_production_monthly_sample.csv"

    result = db.ingest("sodir", files=[sample], refresh=True)
    assert result.rows == 10
    assert result.entities == 0
    assert len(db.entities()) == 1  # an empty download doesn't wipe stored entities


def test_connector_raising_not_implemented_for_entities_is_skipped(tmp_path, data_dir, monkeypatch):
    # The contract is the NotImplementedError, not whether the method is
    # overridden: a connector may override fetch_entities and still lack data.
    def unavailable(self, dest, client=None):
        raise NotImplementedError("no geometry for this source")

    monkeypatch.setattr(sodir.Sodir, "fetch_entities", unavailable)
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    result = ogprod.Store(tmp_path).ingest("sodir", files=[sample])
    assert result.rows == 10
    assert result.entities is None


def test_ingest_skips_entities_for_connector_without_them(tmp_path, data_dir, monkeypatch):
    class NoEntities(base.Source):
        name = "noentities"
        entity_type = get("sodir").entity_type
        docs_url = "https://example.org"

        def parse(self, raw):
            return get("sodir").parse(raw).assign(source="noentities")

    monkeypatch.setitem(base._REGISTRY, "noentities", NoEntities)
    sample = data_dir / "sodir_field_production_monthly_sample.csv"
    result = ogprod.Store(tmp_path).ingest("noentities", files=[sample])
    assert result.rows == 10
    assert result.entities is None


def test_coverage(tmp_path, sodir_client):
    db = ogprod.Store(tmp_path)
    empty = db.coverage()
    assert list(empty.columns) == [
        "source",
        "rows",
        "producing_entities",
        "first_period",
        "last_period",
        "stored_entities",
    ]
    assert empty.empty

    db.ingest("sodir", origin="publisher", client=sodir_client)
    row = db.coverage().iloc[0]
    assert row["source"] == "sodir"
    assert row["rows"] == 10
    assert row["producing_entities"] == 2
    assert row["first_period"] == pd.Timestamp("2024-05-01")
    assert row["stored_entities"] == 1


def test_store_repr_shows_root(tmp_path):
    assert repr(ogprod.Store(tmp_path)) == f"Store({str(tmp_path)!r})"
