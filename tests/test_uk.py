"""Tests for United Kingdom (NSTA) connector (PPRS production + WGS84 fields/facilities/wells)."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ogprod import Store, units
from ogprod.sources import uk

DATA_DIR = Path(__file__).parent / "data"

NSTA_OFFSHORE_FIELDS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 1,
                "FIELDNAME": "CLAIR",
                "FIELDTYPE": "OIL",
                "STATUS": "PRODUCING",
                "DISC_DATE": "1977/07",
                "PROD_DATE": "2005/02",
                "DEPTH_M": "140.0",
                "CURR_OPER": "BP EXPLORATION",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-2.6, 60.6], [-2.4, 60.6], [-2.4, 60.8], [-2.6, 60.8], [-2.6, 60.6]]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 2,
                "FIELDNAME": "MARNOCK-SKUA (oil)",
                "FIELDTYPE": "OIL",
                "STATUS": "PRODUCTION CEASED",
                "DISC_DATE": "1984",
                "PROD_DATE": "1998/07",
                "DEPTH_M": "92.0",
                "CURR_OPER": "SHELL U.K. LIMITED",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[1.5, 57.1], [1.7, 57.1], [1.7, 57.2], [1.5, 57.2], [1.5, 57.1]]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 3,
                "FIELDNAME": "MARNOCK-SKUA (cond)",
                "FIELDTYPE": "COND",
                "STATUS": "PRODUCING",
                "DISC_DATE": "1984/02",
                "PROD_DATE": "1998/07",
                "DEPTH_M": "92.0",
                "CURR_OPER": "SHELL U.K. LIMITED",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[1.7, 57.1], [1.9, 57.1], [1.9, 57.2], [1.7, 57.2], [1.7, 57.1]]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 4,
                "FIELDNAME": "WYTCH FARM",
                "FIELDTYPE": "OIL",
                "STATUS": "PRODUCING",
                "DISC_DATE": "1973/12",
                "PROD_DATE": "1979/03",
                "DEPTH_M": "10.0",
                "CURR_OPER": "PERENCO UK LIMITED",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-1.95, 50.65], [-1.9, 50.65], [-1.9, 50.7], [-1.95, 50.7], [-1.95, 50.65]]
                ],
            },
        },
    ],
}

NSTA_ONSHORE_FIELDS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"OBJECTID": 10, "FIELDNAME": "WYTCH FARM", "FIELDTYPE": "OIL"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-2.05, 50.65], [-1.95, 50.65], [-1.95, 50.7], [-2.05, 50.7], [-2.05, 50.65]]
                ],
            },
        },
        {
            "type": "Feature",
            "properties": {"OBJECTID": 11, "FIELDNAME": "HATFIELD", "FIELDTYPE": "GAS"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [[-1.0, 53.5], [-0.9, 53.5], [-0.9, 53.6], [-1.0, 53.6], [-1.0, 53.5]]
                ],
            },
        },
    ],
}

NSTA_FACILITIES_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 1,
                "FEATURE_ID": "bcb1c849-dc88-4c24-83a9-e95310037e3b",
                "NAME": "ANASURIA",
                "INF_TYPE": "FPSO",
                "REP_GROUP": "ANASURIA OPERATING COMPANY LIMITED",
                "PIPE_SYS": "Fulmar Gas Pipeline",
                "STATUS": "ACTIVE",
                "START_DATE": 845337600000,
                "CURR_ROW": "Y",
            },
            "geometry": {"type": "Point", "coordinates": [0.82, 57.25]},
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 2,
                "FEATURE_ID": "superseded-id",
                "NAME": "OLD ROW",
                "INF_TYPE": "PLATFORM",
                "CURR_ROW": "N",
            },
            "geometry": {"type": "Point", "coordinates": [0.9, 57.3]},
        },
    ],
}

NSTA_OFFSHORE_WELLS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 1,
                "WELLREGNO": "206/08- 1",
                "TARGETFLD": "CLAIR",
                "SUBAREAOP": "BP EXPLORATION",
                "TDOPERATOR": "No Data Available",
                "WELLOPSTAT": "Decomissioned",
                "COMPLESTAT": "Abandoned Phase 3",
                "SPUDDATE": 236822400000,
                "COMPLEDATE": 244512000000,
            },
            "geometry": {"type": "Point", "coordinates": [-2.54, 60.69]},
        },
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 2,
                "WELLREGNO": "C48/30- 21",
                "TARGETFLD": "No Data Available",
                "SUBAREAOP": "No Data Available",
                "TDOPERATOR": "BACTON CCS LIMITED",
                "WELLOPSTAT": "Decomissioned",
                "COMPLESTAT": None,
                "SPUDDATE": 1748563200000,
                "COMPLEDATE": 1757462400000,
            },
            "geometry": {"type": "Point", "coordinates": [1.75, 53.05]},
        },
    ],
}

NSTA_ONSHORE_WELLS_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "OBJECTID": 13713,
                "WELLREGNO": "L100/01- 1",
                "TARGETFLD": "No Data Available",
                "SUBAREAOP": "BP AFRICA LIMITED",
                "TDOPERATOR": "No Data Available",
                "WELLOPSTAT": "Decomissioned",
                "COMPLESTAT": "Abandoned Phase 3",
                "SPUDDATE": -1065139200000,
                "COMPLEDATE": -1064966400000,
                "ONS_COUNTY": "East Sussex",
            },
            "geometry": {"type": "Point", "coordinates": [-0.01, 50.87]},
        }
    ],
}


def test_parse_uk_production_csv_units_and_schema() -> None:
    table = uk.UK().parse_production(DATA_DIR / "nsta_pprs_sample.csv")
    df = table.to_pandas().set_index("entity_id")

    assert len(df) == 6
    assert df["oil_prod"].dtype == "float32"
    assert df["gas_prod"].dtype == "float32"
    assert df["water_inj"].dtype == "float32"
    assert df["gas_inj"].dtype == "float32"

    andrew = df.loc["ANDREW"]
    assert andrew["source"] == "uk"
    assert andrew["entity_type"] == "field"
    assert andrew["field_id"] == "ANDREW"
    assert andrew["region"] == "Central North Sea"
    assert andrew["environment"] == "offshore"
    assert andrew["operator"] == "BP EXPLORATION"
    assert math.isclose(float(andrew["oil_prod"]), 311990 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(andrew["gas_prod"]), 68171 / 31, rel_tol=1e-4)
    assert math.isclose(float(andrew["water_prod"]), 137257 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(andrew["gas_inj"]), 37712 / 31, rel_tol=1e-4)

    # Leap-year February (2000-02 has 29 days).
    alba = df.loc["ALBA"]
    assert math.isclose(float(alba["oil_prod"]), 362869 * units.BBL_PER_M3 / 29, rel_tol=1e-4)
    assert math.isclose(float(alba["water_inj"]), 1032304 * units.BBL_PER_M3 / 29, rel_tol=1e-4)

    # Dry gas + condensate with NULL oil & associated gas.
    cromarty = df.loc["CROMARTY"]
    assert math.isnan(float(cromarty["oil_prod"]))
    assert math.isclose(
        float(cromarty["condensate_prod"]), 971 * units.BBL_PER_M3 / 30, rel_tol=1e-4
    )
    assert math.isclose(float(cromarty["gas_prod"]), 30947 / 30, rel_tol=1e-4)

    # Bracketed sub-field resolves to parent field_id while keeping distinct entity_id.
    clair = df.loc["CLAIR-PHASE 1"]
    assert clair["field_id"] == "CLAIR"
    assert clair["region"] == "West of Shetland"

    # Storage + production pair on HATFIELD stay distinct via UNITNAME and share field_id.
    hatfield_prod = df.loc["HATFIELD MOORS"]
    hatfield_stor = df.loc["HATFIELD MOOR GAS STORAGE INJECTION"]
    assert hatfield_prod["field_id"] == "HATFIELD"
    assert hatfield_stor["field_id"] == "HATFIELD"
    assert hatfield_prod["region"] == "Onshore"
    assert hatfield_prod["environment"] == "onshore"
    assert math.isclose(float(hatfield_prod["gas_prod"]), 18497 / 30, rel_tol=1e-4)
    assert math.isclose(float(hatfield_stor["gas_inj"]), 9000 / 30, rel_tol=1e-4)


def test_parse_uk_production_arcgis_json_and_empty_features() -> None:
    payload = {
        "features": [
            {
                "attributes": {
                    "FIELDNAME": "LEMAN [SHELL][pt. of LEMAN]",
                    "FIELDAREA": "SNS",
                    "LOCATION": "Offshore",
                    "ORGGRPNM": "SHELL U.K. LIMITED",
                    "UNITNAME": "LEMAN SHELL",
                    "PERIODYR": "2025",
                    "PERIODMNTH": "04",
                    "OILPRODM3": None,
                    "GCONDVOL": 300.0,
                    "AGASPROKSM": None,
                    "DGASPROKSM": 60000.0,
                    "WATPRODVOL": 600.0,
                    "INJWATVOL": 0.0,
                    "GASINJVOL": 0.0,
                }
            }
        ]
    }
    table = uk.UK().parse_production(io.BytesIO(json.dumps(payload).encode("utf-8")))
    row = table.to_pandas().iloc[0]
    assert row["entity_id"] == "LEMAN SHELL"
    assert row["field_id"] == "LEMAN"
    assert row["region"] == "Southern North Sea"
    assert math.isclose(float(row["gas_prod"]), 2000.0, rel_tol=1e-4)

    empty = uk.UK().parse_production(io.BytesIO(b'{"features": []}'))
    assert empty.num_rows == 0


def test_uk_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError, match="offshore"):
        uk.UK().discover_production(environment="deepwater")
    with pytest.raises(ValueError, match="not an NSTA PPRS"):
        uk.UK().parse_production(DATA_DIR / "anp_mar_2026_sample.csv")
    with pytest.raises(ValueError, match="not an NSTA PPRS JSON"):
        uk.UK().parse_production(io.BytesIO(b'{"wrong": 1}'))

    bad_csv = "FIELDNAME,PERIODYR,PERIODMNTH,OILPRODM3\nALBA,2025,13,100\n"
    with pytest.raises(ValueError, match="unparseable year/month"):
        uk.UK().parse_production(io.BytesIO(bad_csv.encode("utf-8")))


def test_uk_end_to_end_mock_ingest(tmp_path: Path) -> None:
    pprs_2025 = {
        "features": [
            {
                "attributes": {
                    "OBJECTID": 1,
                    "FIELDNAME": "CLAIR-PHASE 1 [Part of CLAIR]",
                    "FIELDAREA": "WoS",
                    "LOCATION": "Offshore",
                    "ORGGRPNM": "BP EXPLORATION",
                    "UNITNAME": "CLAIR-PHASE 1",
                    "PERIODYR": "2025",
                    "PERIODMNTH": "01",
                    "OILPRODM3": 158987.294928,
                    "GCONDVOL": 0.0,
                    "AGASPROKSM": 3100.0,
                    "DGASPROKSM": 0.0,
                    "WATPRODVOL": 0.0,
                    "INJWATVOL": 158987.294928,
                    "GASINJVOL": 0.0,
                }
            }
        ]
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "PPRS_points" in url and "returnDistinctValues=true" in url:
            return httpx2.Response(200, json={"features": [{"attributes": {"PERIODYR": "2025"}}]})
        if "PPRS_points" in url:
            return httpx2.Response(200, json=pprs_2025)
        if "Offshore_hydrocarbon_fields" in url:
            return httpx2.Response(200, json=NSTA_OFFSHORE_FIELDS_GEOJSON)
        if "Onshore_hydrocarbon_fields" in url:
            return httpx2.Response(200, json=NSTA_ONSHORE_FIELDS_GEOJSON)
        if "surface%20points%20WGS84/FeatureServer?f=json" in url:
            return httpx2.Response(200, json={"layers": [{"id": 7, "name": "SURFACE_20270101"}]})
        if "surface%20points%20WGS84/FeatureServer/7/query" in url:
            return httpx2.Response(200, json=NSTA_FACILITIES_GEOJSON)
        if "offshore_wellbore_top_holes" in url:
            return httpx2.Response(200, json=NSTA_OFFSHORE_WELLS_GEOJSON)
        if "onshore%20petroleum%20wells%20top%20holes" in url:
            return httpx2.Response(200, json=NSTA_ONSHORE_WELLS_GEOJSON)
        return httpx2.Response(404)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    db = Store(tmp_path)
    summary = db.ingest("uk", origin="publisher", client=client)

    assert summary.production_rows == 1
    # 4 offshore (MARNOCK-SKUA oil+cond dissolved) + 2 onshore (WYTCH FARM dissolved) = 4.
    assert summary.geo_counts == {"fields": 4, "facilities": 1, "wells": 3}

    fields = db.fields(source="uk").set_index("field_id")
    assert set(fields.index) == {"CLAIR", "MARNOCK-SKUA", "WYTCH FARM", "HATFIELD"}
    assert fields.loc["CLAIR", "discovery_date"] == "1977-07-01"
    assert fields.loc["CLAIR", "production_start_date"] == "2005-02-01"
    assert fields.loc["MARNOCK-SKUA", "field_name"] == "MARNOCK-SKUA"
    assert fields.loc["MARNOCK-SKUA", "discovery_date"] == "1984-01-01"
    assert fields.loc["WYTCH FARM", "operator"] == "PERENCO UK LIMITED"
    assert fields.loc["HATFIELD", "region"] == "Onshore"

    fac = db.facilities(source="uk").set_index("facility_id")
    assert len(fac) == 1
    assert fac.loc["bcb1c849-dc88-4c24-83a9-e95310037e3b", "export_system"] == "Fulmar Gas Pipeline"
    assert fac.loc["bcb1c849-dc88-4c24-83a9-e95310037e3b", "start_year"] == 1996

    wells = db.wells(source="uk").set_index("well_id")
    assert wells.loc["206/08- 1", "field_id"] == "CLAIR"
    assert wells.loc["206/08- 1", "spud_date"] == "1977-07-04"
    assert wells["field_id"].isna().loc["C48/30- 21"]
    assert wells.loc["C48/30- 21", "operator"] == "BACTON CCS LIMITED"
    assert wells.loc["C48/30- 21", "status"] == "Decommissioned"
    assert wells.loc["L100/01- 1", "spud_date"] == "1936-04-01"
    assert wells.loc["L100/01- 1", "region"] == "East Sussex"
    assert wells.loc["L100/01- 1", "environment"] == "onshore"

    manifest = json.loads((tmp_path / "store" / "manifest.json").read_text(encoding="utf-8"))
    uk_meta = manifest["sources"]["uk"]
    assert uk_meta["country"] == "United Kingdom"
    assert uk_meta["country_code"] == "GB"
    assert uk_meta["publisher"] == "NSTA"


def test_uk_arcgis_pagination_merges_pages(tmp_path: Path) -> None:
    page1 = {
        "exceededTransferLimit": True,
        "features": [{"attributes": {"OBJECTID": 1, "FIELDNAME": "ALBA"}}],
    }
    page2 = {
        "exceededTransferLimit": False,
        "features": [{"attributes": {"OBJECTID": 2, "FIELDNAME": "FORTIES"}}],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=page1 if "resultOffset=0" in str(request.url) else page2)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    dl = uk._fetch_arcgis_paginated(
        client,
        "https://example.invalid/query?where=1%3D1&f=json",
        tmp_path / "pprs.json",
        feature_collection=False,
    )
    merged = json.loads(dl.path.read_text(encoding="utf-8"))
    assert len(merged["features"]) == 2


@given(
    name=st.text(max_size=60),
)
def test_resolve_field_key_is_idempotent(name: str) -> None:
    once = uk.resolve_field_key(name)
    assert uk.resolve_field_key(once) == once
    assert once == once.strip().upper()


@given(
    year=st.integers(min_value=1975, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    oil_m3=st.floats(min_value=0.0, max_value=5e6, allow_nan=False, allow_infinity=False),
    agas_ksm3=st.floats(min_value=0.0, max_value=5e6, allow_nan=False, allow_infinity=False),
    dgas_ksm3=st.floats(min_value=0.0, max_value=5e6, allow_nan=False, allow_infinity=False),
)
def test_uk_rate_conversions_match_calendar_day_averages(
    year: int, month: int, oil_m3: float, agas_ksm3: float, dgas_ksm3: float
) -> None:
    payload = {
        "features": [
            {
                "attributes": {
                    "FIELDNAME": "FORTIES",
                    "UNITNAME": "FORTIES",
                    "PERIODYR": str(year),
                    "PERIODMNTH": f"{month:02d}",
                    "OILPRODM3": oil_m3,
                    "AGASPROKSM": agas_ksm3,
                    "DGASPROKSM": dgas_ksm3,
                }
            }
        ]
    }
    table = uk.UK().parse_production(io.BytesIO(json.dumps(payload).encode("utf-8")))
    row = table.to_pandas().iloc[0]
    days = units.days_in_month(year, month)
    expected_oil = units.monthly_volume_to_rate(oil_m3, days, "m3", "bbl/d")
    expected_gas = (agas_ksm3 + dgas_ksm3) / days
    assert math.isclose(float(row["oil_prod"]), expected_oil, rel_tol=1e-5, abs_tol=1e-4)
    assert math.isclose(float(row["gas_prod"]), expected_gas, rel_tol=1e-5, abs_tol=1e-4)
