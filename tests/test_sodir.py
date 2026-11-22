"""Tests for Norway Sodir connector (field production + FactMaps fields/facilities/wells)."""

from __future__ import annotations

import math
from pathlib import Path

import httpx
import pytest

from ogprod import Store, units
from ogprod.sources import sodir

DATA_DIR = Path(__file__).parent / "data"

SODIR_FIELDS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "fldNpdidField": 26376286,
                "fldName": "JOHAN SVERDRUP",
                "fldMainArea": "North sea",
                "cmpLongName": "Equinor Energy AS",
                "fldCurrentActivitySatus": "Producing",
                "fldHcType": "OIL",
                "fldDiscoveryYear": 2010,
                "unused_prop": "drop-me",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[2.4, 58.7], [2.7, 58.7], [2.7, 58.9], [2.4, 58.9], [2.4, 58.7]]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "fldNpdidField": 46437,
                "fldName": "TROLL",
                "fldMainArea": "North sea",
                "cmpLongName": "Equinor Energy AS",
                "fldCurrentActivitySatus": "Producing",
                "fldHcType": "GAS/OIL",
                "fldDiscoveryYear": 1979,
            },
            "geometry": None,
        },
    ],
}

SODIR_FACILITIES_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "fclNpdidFacility": 271245,
                "fclName": "OSEBERG A",
                "fclKind": "CONDEEP 4 SHAFTS",
                "fclPhase": "IN SERVICE",
                "fclBelongsToName": "OSEBERG",
                "fclCurrentOperatorName": "Equinor Energy AS",
                "fclWaterDepth": 109.0,
                "fclStartupDate": 596937600000,
            },
            "geometry": {"type": "Point", "coordinates": [2.827, 60.492]},
        }
    ],
}

SODIR_WELLS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "wlbNpdidWellbore": 2,
                "wlbWellboreName": "16/2-6",
                "fldNpdidField": 26376286,
                "wlbField": "JOHAN SVERDRUP",
                "wlbMainArea": "NORTH SEA",
                "wlbDrillingOperator": "Equinor Energy AS",
                "wlbStatus": "PRODUCING",
                "wlbEntryDate": 1280620800000,
                "wlbCompletionDate": 1285891200000,
            },
            "geometry": {"type": "Point", "coordinates": [2.6, 58.8]},
        }
    ],
}


def test_parse_sodir_production_units_and_schema() -> None:
    table = sodir.Sodir().parse_production(DATA_DIR / "sodir_field_production_sample.csv")
    df = table.to_pandas().set_index("entity_id")

    assert len(df) == 2
    assert df["oil_prod"].dtype == "float32"
    assert df["gas_prod"].dtype == "float32"

    # Jan 2026 = 31 days.
    js = df.loc["26376286"]
    assert js["entity_type"] == "field"
    assert js["field_id"] == "26376286"
    assert js["environment"] == "offshore"
    # 3.5 Mill Sm3 -> bbl/d
    assert math.isclose(
        float(js["oil_prod"]), 3.5 * 1_000_000 * units.BBL_PER_M3 / 31, rel_tol=1e-4
    )
    # 0.093 Bill Sm3 = 93,000 e3m3 -> 3,000 e3m3/d
    assert math.isclose(float(js["gas_prod"]), 3000.0, rel_tol=1e-4)

    troll = df.loc["46437"]
    # 3.1 Bill Sm3 = 3,100,000 e3m3 -> 100,000 e3m3/d
    assert math.isclose(float(troll["gas_prod"]), 100_000.0, rel_tol=1e-4)


def test_sodir_rejects_onshore_and_foreign_csv() -> None:
    with pytest.raises(ValueError, match="offshore"):
        sodir.Sodir().discover_production(environment="onshore")
    with pytest.raises(ValueError, match="not a Sodir"):
        sodir.Sodir().parse_production(DATA_DIR / "anp_mar_2026_sample.csv")


def test_sodir_end_to_end_mock_ingest(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "field_production_monthly" in url:
            return httpx.Response(
                200, content=(DATA_DIR / "sodir_field_production_sample.csv").read_bytes()
            )
        if "/MapServer/7100/query" in url:
            return httpx.Response(200, json=SODIR_FIELDS_GEOJSON)
        if "/MapServer/6000/query" in url:
            return httpx.Response(200, json=SODIR_FACILITIES_GEOJSON)
        if "/MapServer/5000/query" in url:
            return httpx.Response(200, json=SODIR_WELLS_GEOJSON)
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    db = Store(tmp_path)
    summary = db.ingest("sodir", client=client)

    assert summary.production_rows == 2
    assert summary.geo_counts == {"fields": 2, "facilities": 1, "wells": 1}

    fields = db.fields(source="sodir").set_index("field_id")
    assert fields.loc["26376286", "discovery_date"] == "2010-01-01"
    assert fields.loc["46437"].geometry is None

    fac = db.facilities(source="sodir").set_index("facility_id")
    assert fac.loc["271245", "start_year"] == 1988

    wells = db.wells(source="sodir").set_index("well_id")
    assert wells.loc["2", "field_id"] == "26376286"
