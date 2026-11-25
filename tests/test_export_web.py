"""Tests for Layer-2 web visualization exporter (ogprod.export_web and CLI)."""

from __future__ import annotations

import json
import math
import stat
from datetime import date
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import pytest
import shapely
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

from ogprod import export_web, schema, store
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
    assert by_src["brazil"]["known_gaps"][0]["resolved_in_country_rollup"] is True
    assert by_src["norway"]["latest_oil_bpd"] == 700_000.0
    assert by_src["norway"]["licence_url"] == "https://data.norge.no/nlod/en/2.0"
    assert "NLOD 2.0" in by_src["norway"]["licence"]
    assert "8.777/2016" in by_src["brazil"]["licence"]
    assert by_src["brazil"]["licence_url"].startswith("https://")
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

    capture_path = tmp_path / "tippecanoe_capture.json"
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_tippecanoe = fake_bin_dir / "tippecanoe"
    fake_tippecanoe.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "layers = {}\n"
        "for idx, arg in enumerate(args):\n"
        "    if arg == '-L':\n"
        "        name, file_path = args[idx + 1].split(':', 1)\n"
        "        lines = Path(file_path).read_text(encoding='utf-8').splitlines()\n"
        "        layers[name] = [json.loads(line) for line in lines]\n"
        f"Path({str(capture_path)!r}).write_text(\n"
        "    json.dumps({'args': args, 'layers': layers}), encoding='utf-8'\n"
        ")\n"
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

    captured = json.loads(capture_path.read_text(encoding="utf-8"))
    assert "--drop-rate=1" in captured["args"]
    assert "--drop-densest-as-needed" in captured["args"]
    assert "--minimum-zoom=0" in captured["args"]
    assert "--maximum-zoom=12" in captured["args"]

    layers = captured["layers"]
    assert "tippecanoe" not in layers["fields"][0]
    assert layers["field_centroids"][0]["tippecanoe"] == {"minzoom": 6}
    assert layers["facilities"][0]["tippecanoe"] == {"minzoom": 7}
    assert layers["wells"][0]["tippecanoe"] == {"minzoom": 8}


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


def test_export_web_backfills_brazil_country_gas_and_masks_basin_gap(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"

    br_periods = [date(2003, 6, 1), date(2004, 2, 1), date(2005, 6, 1), date(2005, 7, 1)]
    br_table = base.build_production_table(
        source="brazil",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["7-MRL-1-RJS"] * 4),
        entity_name=pd.Series(["7-MRL-1-RJS"] * 4),
        period=pd.Series(br_periods),
        field_id=pd.Series(["MARLIM"] * 4),
        field_name=pd.Series(["MARLIM"] * 4),
        facility_id=pd.Series(["P-35"] * 4),
        facility_name=pd.Series(["P-35"] * 4),
        basin=pd.Series(["Campos"] * 4),
        region=pd.Series(["Rio de Janeiro"] * 4),
        environment=pd.Series(["offshore"] * 4),
        rates={
            "oil_prod": pd.Series([100_000.0, 105_000.0, 110_000.0, 115_000.0]),
            "gas_prod": pd.Series([40_000.0, 1_500.0, 2_000.0, 48_000.0]),
        },
    )
    write_production(br_table, root=data_dir)

    no_table = base.build_production_table(
        source="norway",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["43778"]),
        entity_name=pd.Series(["TROLL"]),
        period=pd.Series([date(2004, 2, 1)]),
        field_id=pd.Series(["43778"]),
        field_name=pd.Series(["TROLL"]),
        basin=pd.Series(["North Sea"]),
        environment=pd.Series(["offshore"]),
        rates={"oil_prod": pd.Series([200_000.0]), "gas_prod": pd.Series([80_000.0])},
    )
    write_production(no_table, root=data_dir)

    export_web.export_web(data_dir=data_dir, out_dir=out_dir, skip_tiles=True)

    # Country rollup: Brazil gap months use official ANP ppgn-el rates; no phantom months added.
    country_json = json.loads((out_dir / "rollups" / "country_monthly.json").read_text("utf-8"))
    br_country = {pt["p"]: pt for pt in country_json["brazil"]}
    assert sorted(br_country) == ["2003-06-01", "2004-02-01", "2005-06-01", "2005-07-01"]
    assert br_country["2003-06-01"]["gas_prod"] == 40_000.0
    assert math.isclose(br_country["2004-02-01"]["gas_prod"], 1_319_177.220 / 29, abs_tol=0.02)
    assert math.isclose(br_country["2005-06-01"]["gas_prod"], 1_501_361.000 / 30, abs_tol=0.02)
    assert br_country["2005-07-01"]["gas_prod"] == 48_000.0
    assert br_country["2004-02-01"]["oil_prod"] == 105_000.0
    assert country_json["norway"][0]["gas_prod"] == 80_000.0

    country_pq = pq.read_table(out_dir / "rollups" / "country_monthly.parquet").to_pandas()
    br_2004_pq = country_pq.loc[
        (country_pq["source"] == "brazil")
        & (pd.to_datetime(country_pq["period"]).dt.date == date(2004, 2, 1)),
        "gas_prod",
    ].iloc[0]
    assert math.isclose(float(br_2004_pq), 1_319_177.220 / 29, rel_tol=1e-6)

    # Basin rollup: Brazil gap months have gas_prod masked to NULL; oil & Norway untouched.
    basin_json = json.loads((out_dir / "rollups" / "basin_monthly.json").read_text("utf-8"))
    campos = {pt["p"]: pt for pt in basin_json["brazil"]["Campos"]}
    assert campos["2003-06-01"]["gas_prod"] == 40_000.0
    assert "gas_prod" not in campos["2004-02-01"]
    assert campos["2004-02-01"]["oil_prod"] == 105_000.0
    assert "gas_prod" not in campos["2005-06-01"]
    assert campos["2005-07-01"]["gas_prod"] == 48_000.0
    assert basin_json["norway"]["North Sea"][0]["gas_prod"] == 80_000.0

    basin_pq = pq.read_table(out_dir / "rollups" / "basin_monthly.parquet").to_pandas()
    campos_2004 = basin_pq.loc[
        (basin_pq["source"] == "brazil")
        & (pd.to_datetime(basin_pq["period"]).dt.date == date(2004, 2, 1)),
        "gas_prod",
    ].iloc[0]
    assert pd.isna(campos_2004)

    # Field, facility, and Layer-1 well rows preserve reported gas values untouched.
    field_json = json.loads(
        (out_dir / "rollups" / "field_monthly" / "brazil.json").read_text("utf-8")
    )
    marlim = {pt["p"]: pt for pt in field_json["MARLIM"]}
    assert marlim["2004-02-01"]["gas_prod"] == 1_500.0

    layer1_2004 = pq.read_table(
        data_dir / "store" / "production" / "source=brazil" / "year=2004" / "data.parquet"
    ).to_pandas()
    assert float(layer1_2004["gas_prod"].iloc[0]) == 1_500.0


@given(
    minzoom=st.integers(min_value=0, max_value=14),
    count=st.integers(min_value=1, max_value=5),
)
def test_write_geojsonl_layer_attaches_tippecanoe_minzoom(
    tmp_path_factory: pytest.TempPathFactory, minzoom: int, count: int
) -> None:
    dest = tmp_path_factory.mktemp("geojsonl") / "layer.geojsonl"
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(i), 0.0]},
            "properties": {"id": i},
        }
        for i in range(count)
    ]
    written = export_web._write_geojsonl_layer(feats, dest, minzoom=minzoom)
    assert written == count

    rows = [json.loads(line) for line in dest.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == count
    for feat, row in zip(feats, rows, strict=True):
        assert "tippecanoe" not in feat
        assert row["geometry"] == feat["geometry"]
        assert row["properties"] == feat["properties"]
        if minzoom > 0:
            assert row["tippecanoe"] == {"minzoom": minzoom}
        else:
            assert "tippecanoe" not in row


def test_export_web_basin_rollup_falls_back_to_field_basin_and_region(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"

    # Mexico production carries no basin/region; geo/fields supplies basin.
    mx_prod = base.build_production_table(
        source="mexico",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["MALOOB", "DEPLETED"]),
        entity_name=pd.Series(["Maloob", "Depleted"]),
        period=pd.Series([date(2026, 1, 1), date(2026, 1, 1)]),
        field_id=pd.Series(["MALOOB", "DEPLETED"]),
        field_name=pd.Series(["Maloob", "Depleted"]),
        rates={"oil_prod": pd.Series([250_000.0, 500.0])},
    )
    write_production(mx_prod, root=data_dir)

    # Norway production carries no basin/region; geo/fields supplies region only (basin is NA).
    no_prod = base.build_production_table(
        source="norway",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["26376286"]),
        entity_name=pd.Series(["JOHAN SVERDRUP"]),
        period=pd.Series([date(2026, 1, 1)]),
        field_id=pd.Series(["26376286"]),
        field_name=pd.Series(["JOHAN SVERDRUP"]),
        rates={"oil_prod": pd.Series([700_000.0])},
    )
    write_production(no_prod, root=data_dir)

    fields = gpd.GeoDataFrame(
        {
            "source": ["mexico", "norway"],
            "field_id": ["MALOOB", "26376286"],
            "field_name": ["Maloob", "JOHAN SVERDRUP"],
            "basin": ["CUENCAS DEL SURESTE", pd.NA],
            "region": [pd.NA, "North Sea"],
        },
        geometry=[
            shapely.box(-92.3, 19.5, -92.1, 19.7),
            shapely.box(2.4, 58.7, 2.7, 58.9),
        ],
        crs="EPSG:4326",
    )
    write_geo(fields, schema.GeoKind.FIELDS, root=data_dir)

    export_web.export_web(data_dir=data_dir, out_dir=out_dir, skip_tiles=True)

    basin_json = json.loads((out_dir / "rollups" / "basin_monthly.json").read_text("utf-8"))
    assert basin_json["mexico"]["CUENCAS DEL SURESTE"][0]["oil_prod"] == 250_000.0
    assert basin_json["mexico"]["Unassigned"][0]["oil_prod"] == 500.0
    assert basin_json["norway"]["North Sea"][0]["oil_prod"] == 700_000.0

    search = json.loads((out_dir / "search" / "index.json").read_text("utf-8"))
    by_id = {(e["s"], e["id"]): e for e in search if e["t"] == "field"}
    assert by_id[("mexico", "MALOOB")]["b"] == "CUENCAS DEL SURESTE"
    assert by_id[("norway", "26376286")]["b"] == "North Sea"

    poly_feats, _ = export_web._field_tile_features(fields, {})
    by_tile_id = {(f["properties"]["source"], f["properties"]["field_id"]): f for f in poly_feats}
    assert by_tile_id[("mexico", "MALOOB")]["properties"]["basin"] == "CUENCAS DEL SURESTE"
    assert by_tile_id[("norway", "26376286")]["properties"]["region"] == "North Sea"
    assert "basin" not in by_tile_id[("norway", "26376286")]["properties"]


def test_export_web_fields_summary_and_tiles_fallback_to_production_region(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"

    # UK offshore production carries region (FIELDAREA) & operator; geo/fields has region=NA.
    uk_prod = base.build_production_table(
        source="uk",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["CLAIR-PHASE 1", "ARGYLL"]),
        entity_name=pd.Series(["CLAIR-PHASE 1", "ARGYLL"]),
        period=pd.Series([date(2026, 8, 1), date(2026, 8, 1)]),
        field_id=pd.Series(["CLAIR", "ARGYLL"]),
        field_name=pd.Series(["CLAIR", "ARGYLL"]),
        region=pd.Series(["West of Shetland", "Central North Sea"]),
        environment=pd.Series(["offshore", "offshore"]),
        operator=pd.Series(["BP EXPLORATION", "HAMILTON BROTHERS"]),
        rates={"oil_prod": pd.Series([55_000.0, 1_200.0])},
    )
    write_production(uk_prod, root=data_dir)

    uk_fields = gpd.GeoDataFrame(
        {
            "source": ["uk"],
            "field_id": ["CLAIR"],
            "field_name": ["CLAIR"],
            "basin": [pd.NA],
            "region": [pd.NA],
            "environment": ["offshore"],
            "operator": ["BP EXPLORATION"],
        },
        geometry=[shapely.box(-2.6, 60.6, -2.4, 60.8)],
        crs="EPSG:4326",
    )
    write_geo(uk_fields, schema.GeoKind.FIELDS, root=data_dir)

    export_web.export_web(data_dir=data_dir, out_dir=out_dir, skip_tiles=True)

    summary_rows = json.loads((out_dir / "rollups" / "fields_summary.json").read_text("utf-8"))
    by_fid = {r["field_id"]: r for r in summary_rows}
    assert by_fid["CLAIR"]["region"] == "West of Shetland"
    assert by_fid["CLAIR"]["basin"] is None
    assert by_fid["ARGYLL"]["region"] == "Central North Sea"
    assert by_fid["ARGYLL"]["environment"] == "offshore"
    assert by_fid["ARGYLL"]["operator"] == "HAMILTON BROTHERS"

    search = json.loads((out_dir / "search" / "index.json").read_text("utf-8"))
    by_search = {e["id"]: e for e in search if e["t"] == "field"}
    assert by_search["CLAIR"]["b"] == "West of Shetland"
    assert by_search["ARGYLL"]["b"] == "Central North Sea"

    with duckdb.connect() as con:
        store._register_store_views(con, data_dir)
        stats = export_web._load_entity_stats(con)
    poly_feats, _ = export_web._field_tile_features(uk_fields, stats["fields"])
    assert poly_feats[0]["properties"]["region"] == "West of Shetland"


def test_export_web_writes_llms_txt_with_sizes_gaps_and_site_url(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"
    _seed_multi_source_store(data_dir)

    export_web.export_web(
        data_dir=data_dir,
        out_dir=out_dir,
        mirror_repo="mralbu/ogprod",
        revision="v2",
        skip_tiles=True,
        site_url="https://mralbu.github.io/ogprod",
    )

    llms_path = out_dir / "llms.txt"
    assert llms_path.exists()
    content = llms_path.read_text(encoding="utf-8")

    assert content.startswith("# ogprod")
    assert "https://mralbu.github.io/ogprod/data/meta.json" in content
    assert "https://mralbu.github.io/ogprod/data/rollups/country_monthly.json" in content
    assert "https://mralbu.github.io/ogprod/data/rollups/field_monthly/brazil.json" in content
    assert "https://mralbu.github.io/ogprod/data/rollups/field_monthly/norway.json" in content
    assert "https://mralbu.github.io/ogprod/data/rollups/facility_monthly/brazil.json" in content
    # Norway has no facility rows in the seeded store, so no dead link should be emitted.
    assert "facility_monthly/norway.json" not in content
    assert "https://huggingface.co/datasets/mralbu/ogprod/resolve/v2/manifest.json" in content
    assert "2003-07-01 to 2005-06-01" in content
    # Top oil field in the seeded store is Norway's Johan Sverdrup (26376286).
    assert "https://mralbu.github.io/ogprod/?source=norway&entity=field:norway/26376286" in content
    # File sizes should be embedded next to endpoint links.
    assert " B):" in content or " KB):" in content


def test_export_web_cli_site_url_and_empty_store_llms_txt(tmp_path: Path) -> None:
    data_dir = tmp_path / "empty_data"
    out_dir = tmp_path / "empty_web"

    res = CliRunner().invoke(
        cli,
        [
            "export-web",
            "--data-dir",
            str(data_dir),
            "--out-dir",
            str(out_dir),
            "--skip-tiles",
            "--site-url",
            "https://example.org/ogprod/",
        ],
        prog_name=APP_NAME,
    )
    assert res.exit_code == 0, res.output
    content = (out_dir / "llms.txt").read_text(encoding="utf-8")
    assert "No sources are currently present in this export." in content
    assert "https://example.org/ogprod/data/meta.json" in content
