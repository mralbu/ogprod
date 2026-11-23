"""Tests for Norway (Sodir) connector (field production + FactMaps fields/facilities/wells)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import httpx2
import pytest

from ogprod import Store, units
from ogprod.sources import norway

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


def test_parse_norway_production_units_and_schema() -> None:
    table = norway.Norway().parse_production(DATA_DIR / "sodir_field_production_sample.csv")
    df = table.to_pandas().set_index("entity_id")

    assert len(df) == 2
    assert df["oil_prod"].dtype == "float32"
    assert df["gas_prod"].dtype == "float32"

    # Jan 2026 = 31 days.
    js = df.loc["26376286"]
    assert js["source"] == "norway"
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


def test_norway_rejects_onshore_and_foreign_csv() -> None:
    with pytest.raises(ValueError, match="offshore"):
        norway.Norway().discover_production(environment="onshore")
    with pytest.raises(ValueError, match="not a Sodir"):
        norway.Norway().parse_production(DATA_DIR / "anp_mar_2026_sample.csv")


def test_norway_end_to_end_mock_ingest(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "field_production_monthly" in url:
            return httpx2.Response(
                200, content=(DATA_DIR / "sodir_field_production_sample.csv").read_bytes()
            )
        if "/MapServer/7100/query" in url:
            return httpx2.Response(200, json=SODIR_FIELDS_GEOJSON)
        if "/MapServer/6000/query" in url:
            return httpx2.Response(200, json=SODIR_FACILITIES_GEOJSON)
        if "/MapServer/5000/query" in url:
            return httpx2.Response(200, json=SODIR_WELLS_GEOJSON)
        return httpx2.Response(404)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    db = Store(tmp_path)
    summary = db.ingest("norway", origin="publisher", client=client)

    assert summary.production_rows == 2
    assert summary.geo_counts == {"fields": 2, "facilities": 1, "wells": 1}
    assert (tmp_path / "store" / "geo" / "fields" / "norway.parquet").exists()

    fields = db.fields(source="norway").set_index("field_id")
    assert fields.loc["26376286", "discovery_date"] == "2010-01-01"
    assert fields.loc["46437"].geometry is None

    fac = db.facilities(source="norway").set_index("facility_id")
    assert fac.loc["271245", "start_year"] == 1988

    wells = db.wells(source="norway").set_index("well_id")
    assert wells.loc["2", "field_id"] == "26376286"

    manifest = json.loads((tmp_path / "store" / "manifest.json").read_text(encoding="utf-8"))
    no_meta = manifest["sources"]["norway"]
    assert no_meta["country"] == "Norway"
    assert no_meta["country_code"] == "NO"
    assert no_meta["publisher"] == "Sodir"


def test_norway_arcgis_pagination_respects_exceeded_transfer_limit(tmp_path: Path) -> None:
    page1 = {
        "type": "FeatureCollection",
        "exceededTransferLimit": True,
        "features": [
            {
                "type": "Feature",
                "properties": {"wlbNpdidWellbore": 1, "wlbWellboreName": "1/1-1"},
                "geometry": {"type": "Point", "coordinates": [2.0, 60.0]},
            }
        ],
    }
    page2 = {
        "type": "FeatureCollection",
        "exceededTransferLimit": False,
        "features": [
            {
                "type": "Feature",
                "properties": {"wlbNpdidWellbore": 2, "wlbWellboreName": "1/1-2"},
                "geometry": {"type": "Point", "coordinates": [2.1, 60.1]},
            }
        ],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        return httpx2.Response(200, json=page1 if "resultOffset=0" in url else page2)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    dl = norway._fetch_arcgis_geojson(client, 5000, "wlbNpdidWellbore", tmp_path / "wells.geojson")
    merged = json.loads(dl.path.read_text(encoding="utf-8"))
    assert len(merged["features"]) == 2


def test_norway_rejects_unparseable_year_month() -> None:
    import io

    bad_csv = (
        "prfInformationCarrier,prfYear,prfMonth,prfPrdOilNetMillSm3,prfNpdidInformationCarrier\n"
        "TROLL,2026,13,1.0,46437\n"
    )
    with pytest.raises(ValueError, match="unparseable year/month"):
        norway.Norway().parse_production(io.BytesIO(bad_csv.encode("utf-8")))
