"""Tests for the Netherlands NLOG (TNO) connector."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ogprod import audit, export_web, schema, sources, store, units
from ogprod.sources import netherlands

DATA_DIR = Path(__file__).parent / "data"


def _wfs_feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::23031"}},
        "features": features,
    }


def _sample_fields_geojson() -> dict[str, Any]:
    # ED50 / UTM 31N coordinates in the Dutch North Sea & Groningen onshore.
    # Second polygon for A12-FA intentionally forms a self-intersecting bowtie to test repair.
    return _wfs_feature_collection(
        [
            {
                "type": "Feature",
                "properties": {
                    "FIELD_CODE": "A12-FA",
                    "FIELD_NAME": "A12-FA",
                    "HYDROCARBON_MINERAL": "Gas",
                    "STATUS_DESCRIPTION": "Producing",
                    "OPERATOR": "Petrogas E&P Netherlands B.V.",
                    "PRODUCTION_START": "2007-10-01",
                    "LANDSEA": "Sea",
                    "DISCOVERY_YEAR": "1998",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [550000.0, 6130000.0],
                            [552000.0, 6130000.0],
                            [552000.0, 6132000.0],
                            [550000.0, 6132000.0],
                            [550000.0, 6130000.0],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "FIELD_CODE": "GRO",
                    "FIELD_NAME": "Groningen",
                    "HYDROCARBON_MINERAL": "Olie en Gas",
                    "STATUS_DESCRIPTION": "Production ceased",
                    "OPERATOR": "Nederlandse Aardolie Maatschappij B.V.",
                    "PRODUCTION_START": "1963-12-01",
                    "LANDSEA": "Land",
                    "DISCOVERY_YEAR": "1959",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [740000.0, 5900000.0],
                            [744000.0, 5904000.0],
                            [740000.0, 5904000.0],
                            [744000.0, 5900000.0],
                            [740000.0, 5900000.0],
                        ]
                    ],
                },
            },
        ]
    )


def _sample_facilities_geojson() -> dict[str, Any]:
    return _wfs_feature_collection(
        [
            {
                "type": "Feature",
                "properties": {
                    "FACILITY_CODE": "A12-A",
                    "FACILITY_NAME": "A12-A Platform",
                    "FACILITY_TYPE_CODE": "PLF",
                    "FACILITY_TYPE_DESCRIPTION": "Production platform",
                    "OPERATOR": "Petrogas E&P Netherlands B.V.",
                    "STATUS_CODE": "ACT",
                    "STATUS_DESCRIPTION": "Active",
                },
                "geometry": {"type": "Point", "coordinates": [551000.0, 6131000.0]},
            },
            {
                "type": "Feature",
                "properties": {
                    "FACILITY_CODE": "GEO-01",
                    "FACILITY_NAME": "Geothermal Doublet",
                    "FACILITY_TYPE_CODE": "GTI",
                    "FACILITY_TYPE_DESCRIPTION": "Geothermal installation",
                    "OPERATOR": "GeoHeat B.V.",
                    "STATUS_CODE": "ACT",
                    "STATUS_DESCRIPTION": "Active",
                },
                "geometry": {"type": "Point", "coordinates": [600000.0, 5760000.0]},
            },
        ]
    )


def _sample_wells_geojson() -> dict[str, Any]:
    return _wfs_feature_collection(
        [
            {
                "type": "Feature",
                "properties": {
                    "BOREHOLE_CODE": "A12-FA-01-S1",
                    "BOREHOLE_NAME": "A12-FA-01-S1",
                    "WELL_NAME": "A12-FA-01",
                    "BOREHOLE_OR_SIDETRACK_CODE": "SDT",
                    "BOREHOLE_TYPE_CODE": "DEV-HC",
                    "STATUS": "Producing",
                    "CURRENT_OWNER": "Petrogas E&P Netherlands B.V.",
                    "ORIGINAL_OPERATOR": "Wintershall",
                    "ON_OFFSHORE_CODE": "OFF",
                    "FIELD_NAME": "A12-FA",
                    "START_DATE_DRILLING": "2008-03-01",
                    "END_DATE_DRILLING": "2008-05-01",
                },
                "geometry": {"type": "Point", "coordinates": [551200.0, 6131200.0]},
            },
            {
                "type": "Feature",
                "properties": {
                    "BOREHOLE_CODE": "A12-FA-01",
                    "BOREHOLE_NAME": "A12-FA-01",
                    "WELL_NAME": "A12-FA-01",
                    "BOREHOLE_OR_SIDETRACK_CODE": "BRH",
                    "BOREHOLE_TYPE_CODE": "DEV-HC",
                    "STATUS": "Producing",
                    "CURRENT_OWNER": "Petrogas E&P Netherlands B.V.",
                    "ORIGINAL_OPERATOR": "Wintershall",
                    "ON_OFFSHORE_CODE": "OFF",
                    "FIELD_NAME": "A12-FA",
                    "START_DATE_DRILLING": "2007-06-01",
                    "END_DATE_DRILLING": "2007-08-15",
                },
                "geometry": {"type": "Point", "coordinates": [551000.0, 6131000.0]},
            },
            {
                "type": "Feature",
                "properties": {
                    "BOREHOLE_CODE": "A12-FA-02",
                    "BOREHOLE_NAME": "A12-FA-02",
                    "WELL_NAME": "A12-FA-02",
                    "BOREHOLE_OR_SIDETRACK_CODE": "BRH",
                    "BOREHOLE_TYPE_CODE": "DEV-I",
                    "STATUS": "Injecting",
                    "CURRENT_OWNER": "Petrogas E&P Netherlands B.V.",
                    "ORIGINAL_OPERATOR": "Petrogas E&P Netherlands B.V.",
                    "ON_OFFSHORE_CODE": "OFF",
                    "FIELD_NAME": "A12-FA",
                    "START_DATE_DRILLING": "2009-01-10",
                    "END_DATE_DRILLING": "2009-02-20",
                },
                "geometry": {"type": "Point", "coordinates": [551500.0, 6131500.0]},
            },
            {
                "type": "Feature",
                "properties": {
                    "BOREHOLE_CODE": "EXPL-01",
                    "BOREHOLE_NAME": "EXPL-01",
                    "WELL_NAME": None,
                    "BOREHOLE_OR_SIDETRACK_CODE": "BRH",
                    "BOREHOLE_TYPE_CODE": "EXP-HC",
                    "STATUS": "Plugged and abandoned",
                    "CURRENT_OWNER": None,
                    "ORIGINAL_OPERATOR": "NAM",
                    "ON_OFFSHORE_CODE": "ON",
                    "FIELD_NAME": None,
                    "START_DATE_DRILLING": "1985-04-01",
                    "END_DATE_DRILLING": "1985-06-01",
                },
                "geometry": {"type": "Point", "coordinates": [741000.0, 5901000.0]},
            },
            {
                "type": "Feature",
                "properties": {
                    "BOREHOLE_CODE": "SALT-01",
                    "BOREHOLE_NAME": "SALT-01",
                    "WELL_NAME": "SALT-01",
                    "BOREHOLE_OR_SIDETRACK_CODE": "BRH",
                    "BOREHOLE_TYPE_CODE": "DEV-PS",
                    "STATUS": "Active",
                    "CURRENT_OWNER": "SaltCo",
                    "ORIGINAL_OPERATOR": "SaltCo",
                    "ON_OFFSHORE_CODE": "ON",
                    "FIELD_NAME": None,
                    "START_DATE_DRILLING": "2010-01-01",
                    "END_DATE_DRILLING": "2010-02-01",
                },
                "geometry": {"type": "Point", "coordinates": [742000.0, 5902000.0]},
            },
        ]
    )


def test_netherlands_metadata_and_adr_0011_defaults() -> None:
    conn = sources.get("netherlands")
    assert conn.name == "netherlands"
    assert conn.country == "Netherlands"
    assert conn.country_code == "NL"
    assert conn.publisher == "NLOG (TNO)"
    assert conn.entity_type is schema.EntityType.WELL
    assert conn.web_wells is True
    assert conn.search_all_wells is False
    assert "NLOG" in conn.licence
    assert conn.licence_url.startswith("https://www.nlog.nl/")


def test_parse_production_rates_leap_year_and_inactive_filtering() -> None:
    conn = netherlands.Netherlands()
    table = conn.parse_production(DATA_DIR / "netherlands_prod_sample.json")
    df = table.to_pandas().sort_values(["entity_id", "period"]).reset_index(drop=True)

    # Inactive all-zero well and unreported null months are dropped.
    assert len(df) == 3
    assert set(df["entity_id"]) == {"A12-FA-01", "A12-FA-02"}

    jan_prod = df[(df["entity_id"] == "A12-FA-01") & (df["period"] == date(2024, 1, 1))].iloc[0]
    assert jan_prod["source"] == "netherlands"
    assert jan_prod["entity_type"] == "well"
    assert jan_prod["field_id"] == "A12-FA"
    assert jan_prod["environment"] == "offshore"
    assert jan_prod["region"] == "Dutch North Sea"
    # 3100 (1000 Nm3) / 31 days = 100.0 e3m3/d
    assert jan_prod["gas_prod"] == pytest.approx(100.0, rel=1e-5)
    # 158.987294928 Sm3 = 1000 bbl / 31 days
    assert jan_prod["oil_prod"] == pytest.approx(1000.0 / 31.0, rel=1e-5)
    # 31.7974589856 Sm3 = 200 bbl / 31 days
    assert jan_prod["condensate_prod"] == pytest.approx(200.0 / 31.0, rel=1e-5)
    assert jan_prod["water_prod"] == pytest.approx((310.0 * units.BBL_PER_M3) / 31.0, rel=1e-5)

    # Leap year February 2024 divides by 29 days.
    feb_prod = df[(df["entity_id"] == "A12-FA-01") & (df["period"] == date(2024, 2, 1))].iloc[0]
    assert feb_prod["gas_prod"] == pytest.approx(100.0, rel=1e-5)

    # Injection well carries water_inj and gas_inj.
    jan_inj = df[(df["entity_id"] == "A12-FA-02") & (df["period"] == date(2024, 1, 1))].iloc[0]
    assert jan_inj["water_inj"] == pytest.approx(2000.0 / 31.0, rel=1e-5)
    assert jan_inj["gas_inj"] == pytest.approx(20.0, rel=1e-5)
    assert np.isnan(jan_inj["gas_prod"])


def test_parse_production_rejects_invalid_bundle() -> None:
    conn = netherlands.Netherlands()
    with pytest.raises(ValueError, match="streams"):
        conn.parse_production(io.BytesIO(b'{"not": "valid"}'))
    with pytest.raises(ValueError, match="environment"):
        conn.parse_production(io.BytesIO(b'{"environment": "mars", "streams": {}}'))


def test_discover_production_filters_and_chunking() -> None:
    conn = netherlands.Netherlands()
    single = conn.discover_production(year=2024)
    assert len(single) == 2
    assert {r.key for r in single} == {
        "netherlands_2024_onshore.json",
        "netherlands_2024_offshore.json",
    }

    offshore_only = conn.discover_production(year=2024, environment="offshore")
    assert [r.key for r in offshore_only] == ["netherlands_2024_offshore.json"]

    recent = conn.discover_production(recent=True, environment="onshore")
    assert len(recent) == 2
    assert all(r.year_min == r.year_max for r in recent)

    with pytest.raises(ValueError, match="environment"):
        conn.discover_production(year=2024, environment="deepwater")


def test_parse_geo_fields_facilities_and_wells(tmp_path: Path) -> None:
    conn = netherlands.Netherlands()

    fields_path = tmp_path / "fields.geojson"
    fields_path.write_text(json.dumps(_sample_fields_geojson()), encoding="utf-8")
    fields = conn.parse_geo(schema.GeoKind.FIELDS, [fields_path]).set_index("field_id")
    assert set(fields.index) == {"A12-FA", "GRONINGEN"}
    assert fields.crs is not None
    assert fields.crs.to_epsg() == 4326
    assert fields.geometry.is_valid.all()
    assert fields.loc["A12-FA", "environment"] == "offshore"
    assert fields.loc["A12-FA", "region"] == "Dutch North Sea"
    assert fields.loc["A12-FA", "primary_fluid"] == "gas"
    assert fields.loc["A12-FA", "discovery_date"] == "1998-01-01"
    # 2000m x 2000m polygon = 4.0 km^2
    assert fields.loc["A12-FA", "area_km2"] == pytest.approx(4.0, rel=1e-4)
    assert fields.loc["GRONINGEN", "environment"] == "onshore"
    assert fields.loc["GRONINGEN", "primary_fluid"] == "oil_and_gas"

    fac_path = tmp_path / "facilities.geojson"
    fac_path.write_text(json.dumps(_sample_facilities_geojson()), encoding="utf-8")
    facs = conn.parse_geo(schema.GeoKind.FACILITIES, [fac_path])
    # Geothermal installation (GTI) excluded.
    assert list(facs["facility_id"]) == ["A12-A"]
    assert facs.iloc[0]["facility_type"] == "Production platform"

    wells_path = tmp_path / "wells.geojson"
    wells_path.write_text(json.dumps(_sample_wells_geojson()), encoding="utf-8")
    wells = conn.parse_geo(schema.GeoKind.WELLS, [wells_path]).set_index("well_id")
    # Rock salt borehole (DEV-PS) excluded; sidetrack deduplicated in favor of BRH.
    assert set(wells.index) == {"A12-FA-01", "A12-FA-02", "EXPL-01"}
    assert wells.loc["A12-FA-01", "spud_date"] == "2007-06-01"
    assert wells.loc["A12-FA-01", "field_id"] == "A12-FA"
    assert wells.loc["EXPL-01", "operator"] == "NAM"
    assert wells.loc["EXPL-01", "environment"] == "onshore"


def test_netherlands_end_to_end_mock_ingest_audit_and_web_export(tmp_path: Path) -> None:
    sample_bundle = json.loads((DATA_DIR / "netherlands_prod_sample.json").read_text("utf-8"))
    stream_lookup = {
        (prod, ptype): sample_bundle["streams"][col] for col, prod, ptype, _ in netherlands._STREAMS
    }
    fields_bytes = json.dumps(_sample_fields_geojson()).encode("utf-8")
    fac_bytes = json.dumps(_sample_facilities_geojson()).encode("utf-8")
    wells_bytes = json.dumps(_sample_wells_geojson()).encode("utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "/rest/prodfigures/well" in url:
            body = json.loads(request.content.decode("utf-8"))
            if body.get("location") == "L":
                return httpx2.Response(200, json=[])
            rows = stream_lookup.get((body["product"], body["production"]), [])
            return httpx2.Response(200, json=rows)
        if "gdw_ng_field_utm" in url:
            return httpx2.Response(200, content=fields_bytes)
        if "GDW_NG_FACILITY_UTM" in url:
            return httpx2.Response(200, content=fac_bytes)
        if "gdw_ng_wll_all_utm" in url:
            return httpx2.Response(200, content=wells_bytes)
        return httpx2.Response(404)

    data_dir = tmp_path / "data"
    out_dir = tmp_path / "web"
    db = store.Store(data_dir)
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        summary = db.ingest("netherlands", origin="publisher", year=2024, client=client)

    assert summary.production_rows == 3
    assert summary.geo_counts == {"fields": 2, "facilities": 1, "wells": 3}

    report = audit.audit_store(data_dir, source="netherlands", stale_months=100)
    assert report.ok

    export_web.export_web(data_dir=data_dir, out_dir=out_dir, skip_tiles=True)
    meta = json.loads((out_dir / "meta.json").read_text("utf-8"))
    assert [s["id"] for s in meta["sources"]] == ["netherlands"]
    assert meta["sources"][0]["entity_type"] == "well"

    # Per ADR 0011: producing wells indexed in search; non-producing EXPL-01 excluded.
    search = json.loads((out_dir / "search/index.json").read_text("utf-8"))
    well_ids = {item["id"] for item in search if item["t"] == "well" and item["s"] == "netherlands"}
    assert well_ids == {"A12-FA-01", "A12-FA-02"}
    assert len(export_web._well_tile_features(db.wells(source="netherlands"), {})) == 3


@given(
    year=st.integers(min_value=2003, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    gas_e3m3=st.floats(min_value=0.01, max_value=1e6, allow_nan=False, allow_infinity=False),
    oil_m3=st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False),
)
def test_property_rate_conversion_invariants(
    year: int,
    month: int,
    gas_e3m3: float,
    oil_m3: float,
) -> None:
    month_key = netherlands._MONTH_KEYS[month - 1][0]
    bundle = {
        "source": "netherlands",
        "year_start": year,
        "year_end": year,
        "environment": "onshore",
        "streams": {
            "gas_prod": [
                {
                    "operatorNm": "NAM",
                    "wellNm": "GRO-01",
                    "fieldNm": "Groningen",
                    "year": str(year),
                    month_key: gas_e3m3,
                }
            ],
            "oil_prod": [
                {
                    "operatorNm": "NAM",
                    "wellNm": "GRO-01",
                    "fieldNm": "Groningen",
                    "year": str(year),
                    month_key: oil_m3,
                }
            ],
        },
    }
    table = netherlands.Netherlands().parse_production(
        io.BytesIO(json.dumps(bundle).encode("utf-8"))
    )
    assert table.num_rows == 1
    days = units.days_in_month(year, month)
    row = table.to_pandas().iloc[0]
    assert row["gas_prod"] == pytest.approx(gas_e3m3 / days, rel=1e-5)
    assert row["oil_prod"] == pytest.approx((oil_m3 * units.BBL_PER_M3) / days, rel=1e-5)


@given(current_year=st.integers(min_value=2005, max_value=2040))
def test_property_year_ranges_partition_history(current_year: int) -> None:
    ranges = netherlands._year_ranges(year=None, recent=False, current_year=current_year)
    covered: list[int] = []
    for y0, y1 in ranges:
        assert y0 <= y1
        covered.extend(range(y0, y1 + 1))
    assert covered == list(range(netherlands.FIRST_YEAR, current_year + 1))
    # Last two years are always individual yearly bundles for incremental --recent refreshes.
    assert ranges[-2:] == [(current_year - 1, current_year - 1), (current_year, current_year)]
