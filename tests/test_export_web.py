"""Tests for Layer-2 web visualization exporter (ogprod.export_web and CLI)."""

from __future__ import annotations

import json
import math
import stat
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pytest
import shapely
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

from ogprod import export_web, schema
from ogprod.cli import APP_NAME, cli
from ogprod.sources import base
from ogprod.store import write_geo, write_production


def _seed_multi_source_store(root: Path) -> None:
    br_table = base.build_production_table(
        source="brazil",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["7-BUZ-1-RJS", "7-BUZ-1-RJS"]),
        entity_name=pd.Series(["7-BUZ-1-RJS", "7-BUZ-1-RJS"]),
        period=pd.Series([date(2025, 12, 1), date(2026, 1, 1)]),
        field_id=pd.Series(["BUZIOS", "BUZIOS"]),
        field_name=pd.Series(["BÚZIOS", "BÚZIOS"]),
        facility_id=pd.Series(["FPSO ALMIRANTE BARROSO", "FPSO ALMIRANTE BARROSO"]),
        facility_name=pd.Series(["FPSO Almirante Barroso", "FPSO Almirante Barroso"]),
        basin=pd.Series(["Santos", "Santos"]),
        region=pd.Series(["Rio de Janeiro", "Rio de Janeiro"]),
        environment=pd.Series(["offshore", "offshore"]),
        rates={
            "oil_prod": pd.Series([90_000.0, 120_000.0]),
            "gas_prod": pd.Series([3_000.0, 3_600.0]),
            "water_inj": pd.Series([50_000.0, 55_000.0]),
        },
    )
    write_production(br_table, root=root)

    no_table = base.build_production_table(
        source="norway",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["26376286"]),
        entity_name=pd.Series(["JOHAN SVERDRUP"]),
        period=pd.Series([date(2026, 1, 1)]),
        field_id=pd.Series(["26376286"]),
        field_name=pd.Series(["JOHAN SVERDRUP"]),
        environment=pd.Series(["offshore"]),
        rates={"oil_prod": pd.Series([700_000.0]), "gas_prod": pd.Series([10_000.0])},
    )
    write_production(no_table, root=root)

    fields = gpd.GeoDataFrame(
        {
            "source": ["brazil", "norway", "norway"],
            "field_id": ["BUZIOS", "26376286", "NULL_GEOM_FIELD"],
            "field_name": ["BÚZIOS", "JOHAN SVERDRUP", "RELINQUISHED FIELD"],
            "basin": ["Santos", "North Sea", "North Sea"],
            "operator": ["PETROBRAS", "Equinor", "Equinor"],
            "environment": ["offshore", "offshore", "offshore"],
        },
        geometry=[
            shapely.box(-42.7, -24.7, -42.4, -24.5),
            shapely.box(2.4, 58.7, 2.7, 58.9),
            None,
        ],
        crs="EPSG:4326",
    )
    write_geo(fields, schema.GeoKind.FIELDS, root=root)

    facilities = gpd.GeoDataFrame(
        {
            "source": ["brazil"],
            "facility_id": ["FPSO ALMIRANTE BARROSO"],
            "facility_name": ["FPSO Almirante Barroso"],
            "facility_type": ["FPSO"],
            "fields": ["BÚZIOS"],
            "basin": ["Santos"],
            "operator": ["PETROBRAS"],
            "oil_capacity_bpd": [150_000.0],
            "gas_capacity_e3m3d": [6_000.0],
        },
        geometry=[shapely.Point(-42.55, -24.6)],
        crs="EPSG:4326",
    )
    write_geo(facilities, schema.GeoKind.FACILITIES, root=root)

    wells = gpd.GeoDataFrame(
        {
            "source": ["brazil"],
            "well_id": ["7-BUZ-1-RJS"],
            "well_name": ["7-BUZ-1-RJS"],
            "field_id": ["BUZIOS"],
            "field_name": ["BÚZIOS"],
            "basin": ["Santos"],
            "environment": ["offshore"],
            "status": ["Produzindo"],
        },
        geometry=[shapely.Point(-42.52, -24.58)],
        crs="EPSG:4326",
    )
    write_geo(wells, schema.GeoKind.WELLS, root=root)


def test_export_web_generates_meta_rollups_fpso_utilization_and_search(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"
    _seed_multi_source_store(data_dir)

    summary = export_web.export_web(
        data_dir=data_dir,
        out_dir=out_dir,
        mirror_repo="mralbu/ogprod",
        revision="v2",
        skip_tiles=True,
    )
    assert summary.sources == ("brazil", "norway")
    assert summary.has_tiles is False
    assert summary.search_entries == 5

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["schema_version"] == 2
    assert meta["mirror"]["base_url"] == "https://huggingface.co/datasets/mralbu/ogprod/resolve/v2"
    by_src = {s["id"]: s for s in meta["sources"]}
    assert by_src["brazil"]["years"] == [2025, 2026]
    assert len(by_src["brazil"]["known_gaps"]) == 1
    assert by_src["brazil"]["known_gaps"][0]["start"] == "2003-07-01"
    assert by_src["norway"]["latest_oil_bpd"] == 700_000.0
    assert by_src["brazil"]["bbox"] is not None

    for name in ("country_monthly", "basin_monthly", "field_monthly", "facility_monthly"):
        pf = out_dir / "rollups" / f"{name}.parquet"
        assert pf.exists()
        assert pq.read_metadata(pf).num_rows > 0

    fac_summary = json.loads((out_dir / "rollups" / "facilities_summary.json").read_text("utf-8"))
    assert len(fac_summary) == 1
    assert math.isclose(fac_summary[0]["oil_utilization"], 0.8, rel_tol=1e-4)
    assert math.isclose(fac_summary[0]["gas_utilization"], 0.6, rel_tol=1e-4)

    fac_monthly = json.loads(
        (out_dir / "rollups" / "facility_monthly" / "brazil.json").read_text("utf-8")
    )
    series = fac_monthly["FPSO ALMIRANTE BARROSO"]
    assert len(series) == 2
    assert math.isclose(series[0]["oil_util"], 0.6, rel_tol=1e-4)
    assert math.isclose(series[1]["oil_util"], 0.8, rel_tol=1e-4)

    search = json.loads((out_dir / "search" / "index.json").read_text("utf-8"))
    well_entry = next(item for item in search if item["t"] == "well")
    assert well_entry["id"] == "7-BUZ-1-RJS"
    assert well_entry["fy"] == 2025
    assert well_entry["ly"] == 2026


def test_export_web_invokes_tippecanoe_and_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"
    _seed_multi_source_store(data_dir)

    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_tippecanoe = fake_bin_dir / "tippecanoe"
    fake_tippecanoe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "out = Path(args[args.index('-o') + 1])\n"
        "out.write_bytes(b'PMTiles\\x03fake')\n",
        encoding="utf-8",
    )
    fake_tippecanoe.chmod(fake_tippecanoe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}:{Path('/usr/bin')}")

    res = CliRunner().invoke(
        cli,
        ["export-web", "--data-dir", str(data_dir), "--out-dir", str(out_dir)],
        prog_name=APP_NAME,
    )
    assert res.exit_code == 0, res.output
    assert "tiles=yes" in res.output
    pmtiles = out_dir / "tiles" / "ogprod.pmtiles"
    assert pmtiles.exists()
    assert pmtiles.read_bytes().startswith(b"PMTiles")


def test_source_bbox_prefers_fields_over_overseas_facilities() -> None:
    fields = gpd.GeoDataFrame(
        {"source": ["norway"], "field_id": ["1"], "field_name": ["TROLL"]},
        geometry=[shapely.box(2.0, 58.0, 4.0, 62.0)],
        crs="EPSG:4326",
    )
    facilities = gpd.GeoDataFrame(
        {"source": ["norway"], "facility_id": ["416247"], "facility_name": ["COL DE LARRAU"]},
        geometry=[shapely.Point(0.93, 43.02)],
        crs="EPSG:4326",
    )
    wells = schema.empty_geo(schema.GeoKind.WELLS)
    bbox = export_web._source_bbox("norway", (fields, facilities, wells))
    assert bbox == [2.0, 58.0, 4.0, 62.0]

    empty_fields = schema.empty_geo(schema.GeoKind.FIELDS)
    fallback = export_web._source_bbox("norway", (empty_fields, facilities, wells))
    assert fallback == [0.93, 43.02, 0.93, 43.02]


@given(
    lon=st.floats(min_value=-179.0, max_value=179.0, allow_nan=False, allow_infinity=False),
    lat=st.floats(min_value=-89.0, max_value=89.0, allow_nan=False, allow_infinity=False),
    span=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_tile_features_build_geojson_dicts_matching_to_geojson(
    lon: float, lat: float, span: float
) -> None:
    poly = shapely.box(lon, lat, lon + span, lat + span)
    pt = shapely.Point(lon, lat)

    fields = gpd.GeoDataFrame(
        {"source": ["brazil"], "field_id": ["F1"], "field_name": ["Field 1"]},
        geometry=[poly],
        crs="EPSG:4326",
    )
    facs = gpd.GeoDataFrame(
        {"source": ["brazil"], "facility_id": ["P1"], "facility_name": ["FPSO 1"]},
        geometry=[pt],
        crs="EPSG:4326",
    )
    wells = gpd.GeoDataFrame(
        {"source": ["brazil"], "well_id": ["W1"], "well_name": ["Well 1"]},
        geometry=[pt],
        crs="EPSG:4326",
    )

    poly_feats, centroid_feats = export_web._field_tile_features(fields, {})
    fac_feats = export_web._facility_tile_features(facs, {})
    well_feats = export_web._well_tile_features(wells, {})

    for feat, expected in (
        (poly_feats[0], poly),
        (centroid_feats[0], poly.representative_point()),
        (fac_feats[0], pt),
        (well_feats[0], pt),
    ):
        assert isinstance(feat["geometry"], dict)
        serialized = json.loads(json.dumps(feat["geometry"]))
        assert serialized == json.loads(shapely.to_geojson(expected))
