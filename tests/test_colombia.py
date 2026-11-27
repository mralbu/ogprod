"""Tests for the Colombia ANH (datos.gov.co + Geovisor) connector."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx2
import pandas as pd
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ogprod import schema, sources, units
from ogprod.sources import colombia

SAMPLE_JSON = Path(__file__).parent / "data" / "colombia_prod_sample.json"


def _sample_basins_geojson() -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"NOMBRE": "LLANOS ORIENTALES", "TIPO": "MADURA"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-73.0, 2.0], [-70.0, 2.0], [-70.0, 6.0], [-73.0, 6.0], [-73.0, 2.0]]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"NOMBRE": "GUAJIRA OFFSHORE", "TIPO": "OFFSHORE"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-74.0, 11.0], [-71.0, 11.0], [-71.0, 13.0], [-74.0, 13.0], [-74.0, 11.0]]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {"NOMBRE": "AREA NO PROSPECTIVA", "TIPO": "NO APLICA"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-76.0, 1.0], [-74.0, 1.0], [-74.0, 2.0], [-76.0, 2.0], [-76.0, 1.0]]
                    ],
                },
            },
        ],
    }


def test_colombia_registered_and_metadata() -> None:
    src = sources.get("colombia")
    assert src.name == "colombia"
    assert src.country == "Colombia"
    assert src.country_code == "CO"
    assert src.publisher == "ANH"
    assert src.entity_type is schema.EntityType.FIELD
    assert src.licence == "CC BY-SA 4.0"
    assert src.web_wells is True
    assert src.search_all_wells is False


def test_discover_production_year_recent_and_full() -> None:
    src = colombia.Colombia()
    single = src.discover_production(year=2024)
    assert len(single) == 1
    assert single[0].key == "colombia_2024.json"
    assert single[0].year_min == 2024
    assert single[0].year_max == 2024

    recent = src.discover_production(recent=True)
    assert len(recent) == 2
    assert recent[0].year_max is not None
    assert recent[1].year_min is not None
    assert recent[0].year_max == recent[1].year_min - 1

    full = src.discover_production()
    assert full[0].year_min == colombia.FIRST_YEAR
    assert full[-1].year_max is not None
    assert full[-1].year_max >= 2026

    with pytest.raises(ValueError, match="environment"):
        src.discover_production(environment="mars")


def test_parse_production_bundle_aggregates_and_converts() -> None:
    src = colombia.Colombia()
    table = src.parse_production(SAMPLE_JSON)
    df = table.to_pandas().set_index(["field_id", "period"])

    # Blank campo row dropped -> 6 distinct (field_id, period) rows.
    assert len(df) == 6

    # Rubiales Jan 2024 (31 days) & Feb 2024 (29 days leap year).
    rub_jan = df.loc[("RUBIALES", date(2024, 1, 1))]
    assert rub_jan["oil_prod"] == pytest.approx(3_077_162.0 / 31.0, rel=1e-5)
    assert rub_jan["region"] == "Meta"
    assert rub_jan["operator"] == "ECOPETROL S.A."
    assert rub_jan["gas_prod"] != rub_jan["gas_prod"]  # NaN / null

    rub_feb = df.loc[("RUBIALES", date(2024, 2, 1))]
    assert rub_feb["oil_prod"] == pytest.approx(2_693_773.0 / 29.0, rel=1e-5)

    # Multi-municipality field sums both oil and gas; picks highest-volume municipality's region.
    teca = df.loc[("AREA TECA-COCORNA", date(2024, 1, 1))]
    assert teca["oil_prod"] == pytest.approx((10_000.0 + 5_500.0) / 31.0, rel=1e-5)
    assert teca["gas_prod"] == pytest.approx(
        units.monthly_volume_to_rate(6_209.0 + 6_275.0, 31, "mcf", "e3m3/d"),
        rel=1e-5,
    )
    assert teca["region"] == "Antioquia"

    # Accented field name normalized into field_id while preserving display field_name.
    balcon = df.loc[("BALCON", date(2024, 1, 1))]
    assert balcon["field_name"] == "BALCÓN"
    assert balcon["region"] == "Huila"

    # Gas-only offshore field & DEPARTAMENTO NN -> None.
    chuchupa = df.loc[("CHUCHUPA", date(2024, 1, 1))]
    assert chuchupa["gas_prod"] == pytest.approx(
        units.monthly_volume_to_rate(2_590_271.0, 31, "mcf", "e3m3/d"),
        rel=1e-5,
    )
    assert chuchupa["region"] == "Guajira"

    unalloc = df.loc[("UNALLOCATED GAS", date(2024, 1, 1))]
    assert pd.isna(unalloc["region"])


def test_parse_standalone_socrata_csvs() -> None:
    src = colombia.Colombia()
    oil_csv = io.BytesIO(
        b'"vigencia","mes","departamento","operadora","campo","produccion_bls"\n'
        b'"2024","4","META","ECOPETROL S.A.","RUBIALES","3,000,000.00"\n'
    )
    oil_tbl = src.parse_production(oil_csv)
    assert oil_tbl.num_rows == 1
    assert oil_tbl.column("oil_prod")[0].as_py() == pytest.approx(100_000.0, rel=1e-5)

    gas_csv = io.BytesIO(
        b'"vigencia","mes","departamento","operadora","campo","produccionkpc"\n'
        b'"2024","4","GUAJIRA","HOCOL S.A.","CHUCHUPA","300,000.00"\n'
    )
    gas_tbl = src.parse_production(gas_csv)
    assert gas_tbl.num_rows == 1
    assert gas_tbl.column("gas_prod")[0].as_py() == pytest.approx(
        units.monthly_volume_to_rate(300_000.0, 30, "mcf", "e3m3/d"),
        rel=1e-5,
    )


def test_fetch_production_paginates_and_bundles(tmp_path: Path) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if colombia._OIL_DATASET_ID in url:
            return httpx2.Response(
                200,
                json=[
                    {
                        "vigencia": "2024",
                        "mes": "1",
                        "departamento": "META",
                        "operadora": "ECOPETROL S.A.",
                        "campo": "RUBIALES",
                        "produccion_bls": "310,000.00",
                    }
                ],
            )
        if colombia._GAS_DATASET_ID in url:
            return httpx2.Response(
                200,
                json=[
                    {
                        "vigencia": "2024",
                        "mes": "1",
                        "departamento": "GUAJIRA",
                        "operadora": "HOCOL S.A.",
                        "campo": "CHUCHUPA",
                        "produccionkpc": "31,000.00",
                    }
                ],
            )
        return httpx2.Response(404)

    src = colombia.Colombia()
    remote = src.discover_production(year=2024)[0]
    dest = tmp_path / remote.key
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        dl = src.fetch_production(remote, dest, client=client)

    assert dl.path.exists()
    assert dl.size_bytes > 0
    table = src.parse_production(dl.path)
    assert table.num_rows == 2


def _sample_yacimientos_geojson() -> dict[str, object]:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "CAMPO_ID": "101",
                    "CAMPO": "RUBIALES",
                    "FORMACION": "CARBONERA C7",
                    "ESTADO_YACIMIENTO": "ACTIVO",
                    "TIPO_HIDROCARBURO": "PETROLEO",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-71.50, 3.75],
                            [-71.40, 3.75],
                            [-71.40, 3.85],
                            [-71.50, 3.85],
                            [-71.50, 3.75],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "CAMPO_ID": "101",
                    "CAMPO": "RUBIALES",
                    "FORMACION": "CARBONERA C5",
                    "ESTADO_YACIMIENTO": "ACTIVO",
                    "TIPO_HIDROCARBURO": "PETROLEO",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-71.45, 3.78],
                            [-71.35, 3.78],
                            [-71.35, 3.88],
                            [-71.45, 3.88],
                            [-71.45, 3.78],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "CAMPO_ID": "202",
                    "CAMPO": "CASTILLA UNIFICADO",
                    "FORMACION": "GUADALUPE",
                    "ESTADO_YACIMIENTO": "ACTIVO",
                    "TIPO_HIDROCARBURO": "PETROLEO",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-72.80, 3.80],
                            [-72.70, 3.80],
                            [-72.70, 3.90],
                            [-72.80, 3.90],
                            [-72.80, 3.80],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "CAMPO_ID": "303",
                    "CAMPO": "UNIFICADO COSTAYACO - VONU",
                    "FORMACION": "CABALLOS",
                    "ESTADO_YACIMIENTO": "ACTIVO",
                    "TIPO_HIDROCARBURO": "PETROLEO",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-72.30, 3.50],
                            [-72.20, 3.50],
                            [-72.20, 3.60],
                            [-72.30, 3.60],
                            [-72.30, 3.50],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "CAMPO_ID": "999",
                    "CAMPO": "RUTY ONLY FIELD",
                    "FORMACION": "MIRADOR",
                    "ESTADO_YACIMIENTO": "ACTIVO",
                    "TIPO_HIDROCARBURO": "PETROLEO-GAS",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-72.00, 4.00],
                            [-71.95, 4.00],
                            [-71.95, 4.05],
                            [-72.00, 4.05],
                            [-72.00, 4.00],
                        ]
                    ],
                },
            },
        ],
    }


def test_fetch_and_parse_geo_fields_with_basins(tmp_path: Path) -> None:
    oil_csv = (
        "campo,departamento,operadora,longitud,latitud,last_year\n"
        "RUBIALES,META,ECOPETROL S.A.,-71.4656,3.7952,2026\n"
        "CASTILLA,META,ECOPETROL S.A.,-72.75,3.85,2026\n"
        "CASTILLA NORTE,META,ECOPETROL S.A.,-72.72,3.88,2026\n"
        "COSTAYACO,PUTUMAYO,GRAN TIERRA,-72.90,3.10,2026\n"
        "NO COORD FIELD,META,ECOPETROL S.A.,0,0,2025\n"
        "NON PROSPECTIVE,CAUCA,OP,-75.0,1.5,2024\n"
    )
    gas_csv = (
        "campo,departamento,operadora,longitud,latitud,last_year\n"
        "RUBIALES,META,ECOPETROL S.A.,-71.4656,3.7952,2026\n"
        "CHUCHUPA,GUAJIRA,HOCOL S.A.,-72.8300,11.8111,2026\n"
    )
    basins_bytes = json.dumps(_sample_basins_geojson()).encode("utf-8")
    yac_bytes = json.dumps(_sample_yacimientos_geojson()).encode("utf-8")

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if colombia._OIL_DATASET_ID in url:
            return httpx2.Response(200, text=oil_csv)
        if colombia._GAS_DATASET_ID in url:
            return httpx2.Response(200, text=gas_csv)
        if "ANH_InsGDB/MapServer/4" in url:
            return httpx2.Response(200, content=basins_bytes)
        if "Yacimientos/MapServer/WFSServer" in url:
            return httpx2.Response(200, content=yac_bytes)
        return httpx2.Response(404)

    src = colombia.Colombia()
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        downloads = src.fetch_geo(schema.GeoKind.FIELDS, tmp_path, client=client)

    gdf = src.parse_geo(schema.GeoKind.FIELDS, [d.path for d in downloads]).set_index("field_id")
    # 7 Socrata fields + 1 unclaimed RUTY-only field (claimed aliases CASTILLA UNIFICADO &
    # UNIFICADO COSTAYACO - VONU are dropped so 0-rate aliases never cover active fields).
    assert len(gdf) == 8
    assert "CASTILLA UNIFICADO" not in gdf.index
    assert "UNIFICADO COSTAYACO - VONU" not in gdf.index

    # Exact match: Rubiales gets dissolved RUTY polygon + area_km2 + field_code + status.
    rub = gdf.loc["RUBIALES"]
    assert rub.geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert rub["field_code"] == "101"
    assert rub["status"] == "Activo"
    assert rub["area_km2"] > 100.0
    assert rub["basin"] == "Llanos Orientales"
    assert rub["environment"] == "onshore"
    assert rub["region"] == "Meta"
    assert rub["primary_fluid"] == "oil_and_gas"

    # Loose UNIFICADO match (CASTILLA) and spatial point-in-polygon match (CASTILLA NORTE).
    assert gdf.loc["CASTILLA"].geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert gdf.loc["CASTILLA"]["field_code"] == "202"
    assert gdf.loc["CASTILLA NORTE"].geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert gdf.loc["CASTILLA NORTE"]["field_code"] == "202"

    # Unique UNIFICADO token match (COSTAYACO -> UNIFICADO COSTAYACO - VONU).
    assert gdf.loc["COSTAYACO"].geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert gdf.loc["COSTAYACO"]["field_code"] == "303"

    # Unreconciled field keeps geometry=None while preserving basin & environment from its point.
    chu = gdf.loc["CHUCHUPA"]
    assert chu.geometry is None
    assert chu["basin"] == "Guajira Offshore"
    assert chu["environment"] == "offshore"
    assert chu["primary_fluid"] == "gas"

    # Unclaimed RUTY-only field retains its polygon, basin, and primary_fluid.
    ruty_only = gdf.loc["RUTY ONLY FIELD"]
    assert ruty_only.geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert ruty_only["basin"] == "Llanos Orientales"
    assert ruty_only["primary_fluid"] == "oil_and_gas"

    no_coord = gdf.loc["NO COORD FIELD"]
    assert no_coord.geometry is None
    assert no_coord["primary_fluid"] == "oil"

    non_prosp = gdf.loc["NON PROSPECTIVE"]
    assert non_prosp.geometry is None
    assert non_prosp["basin"] is None or str(non_prosp["basin"]) == "<NA>"

    with pytest.raises(NotImplementedError):
        src.fetch_geo(schema.GeoKind.FACILITIES, tmp_path)


def test_fetch_and_parse_wells_geo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(colombia, "_SGC_WELLS_CHUNK_SIZE", 2)
    features_by_range = {
        (10, 20): [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-71.4817, 3.8654, 0, None]},
                "properties": {
                    "UWI": "RUBI0087H",
                    "WELL_NAME": "RUBIALES-87H",
                    "WELL_COUNT": "COLOMBIA",
                    "DEPARTAMEN": "META",
                    "FIELD_ABRE": "RUBIALES",
                    "GEOLOGIC_P": "LLANOS ORIENTALES",
                    "OPERATOR_W": "PENDIENTE",
                    "COMPANY_CO": "ECOPETROL S.A.",
                    "WELL_STA_1": "PRODUCTOR ACTIVO",
                    "WELL_SPUD_": -2208970800000,  # 1900-01-01 sentinel -> null
                    "WELL_COMPL": 1206403200000,  # 2008-03-25
                    "VISIBLE": "SI",
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-72.5, 11.8, 120, None]},
                "properties": {
                    "UWI": "CHUC0001",
                    "WELL_NAME": "CHUCHUPA-1",
                    "WELL_COUNT": "COLOMBIA",
                    "DEPARTAMEN": "LA GUAJIRA",
                    "FIELD_ABRE": "CHUCHUPA",
                    "GEOLOGIC_P": "GUAJIRA-OFFSHORE",
                    "OPERATOR_W": "CHEVRON",
                    "COMPANY_CO": "ECOPETROL S.A.",
                    "WELL_STA_1": "SIN ESTADO",
                    "WELL_SPUD_": 1205557200000,  # 2008-03-15
                    "WELL_COMPL": None,
                    "VISIBLE": "SI",
                },
            },
        ],
        (30, 50): [
            # Duplicate UWI with missing geometry should lose to the geolocated row above.
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "UWI": "RUBI0087H",
                    "WELL_NAME": "RUBIALES-87H",
                    "WELL_COUNT": "COLOMBIA",
                    "DEPARTAMEN": "META",
                    "FIELD_ABRE": None,
                    "GEOLOGIC_P": "LLANOS ORIENTALES",
                    "OPERATOR_W": None,
                    "COMPANY_CO": None,
                    "WELL_STA_1": None,
                    "WELL_SPUD_": None,
                    "WELL_COMPL": None,
                    "VISIBLE": "SI",
                },
            },
            # Foreign border well -> filtered out.
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-70.0, 8.0]},
                "properties": {
                    "UWI": "VENZ0001",
                    "WELL_NAME": "VENEZUELA-1",
                    "WELL_COUNT": "VENEZUELA",
                    "VISIBLE": "SI",
                },
            },
            # Hidden well -> filtered out.
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-72.0, 4.0]},
                "properties": {
                    "UWI": "HIDN0001",
                    "WELL_NAME": "HIDDEN-1",
                    "WELL_COUNT": "COLOMBIA",
                    "VISIBLE": "NO",
                },
            },
        ],
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "returnIdsOnly=true" in url:
            return httpx2.Response(
                200, json={"objectIdFieldName": "OBJECTID", "objectIds": [50, 10, 30, 20]}
            )
        if "OBJECTID%3E%3D10" in url and "OBJECTID%3C%3D20" in url:
            return httpx2.Response(
                200, json={"type": "FeatureCollection", "features": features_by_range[(10, 20)]}
            )
        if "OBJECTID%3E%3D30" in url and "OBJECTID%3C%3D50" in url:
            return httpx2.Response(
                200, json={"type": "FeatureCollection", "features": features_by_range[(30, 50)]}
            )
        return httpx2.Response(404)

    src = colombia.Colombia()
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        downloads = src.fetch_geo(schema.GeoKind.WELLS, tmp_path, client=client)

    assert len(downloads) == 1
    assert downloads[0].path.name == "colombia_wells.geojson"

    gdf = src.parse_geo(schema.GeoKind.WELLS, [d.path for d in downloads]).set_index("well_id")
    assert set(gdf.index) == {"RUBI0087H", "CHUC0001"}

    rub = gdf.loc["RUBI0087H"]
    assert rub.geometry.geom_type == "Point"
    assert not rub.geometry.has_z
    assert rub["well_name"] == "RUBIALES-87H"
    assert rub["field_id"] == "RUBIALES"
    assert rub["basin"] == "Llanos Orientales"
    assert rub["region"] == "Meta"
    assert rub["environment"] == "onshore"
    assert rub["operator"] == "ECOPETROL S.A."
    assert rub["status"] == "Productor Activo"
    assert pd.isna(rub["spud_date"])
    assert rub["completion_date"] == "2008-03-25"

    chu = gdf.loc["CHUC0001"]
    assert chu["basin"] == "Guajira Offshore"
    assert chu["region"] == "Guajira"
    assert chu["environment"] == "offshore"
    assert chu["operator"] == "CHEVRON"
    assert pd.isna(chu["status"])
    assert chu["spud_date"] == "2008-03-15"


@given(
    year=st.integers(min_value=2014, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    oil_parts=st.lists(
        st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=4,
    ),
    gas_parts=st.lists(
        st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=4,
    ),
)
def test_municipality_aggregation_and_unit_conversion_invariant(
    year: int,
    month: int,
    oil_parts: list[float],
    gas_parts: list[float],
) -> None:
    bundle = {
        "oil": [
            {
                "vigencia": str(year),
                "mes": str(month),
                "departamento": "META",
                "operadora": "OP",
                "campo": "CAMPO TEST",
                "produccion_bls": f"{val:,.2f}",
            }
            for val in oil_parts
        ],
        "gas": [
            {
                "vigencia": str(year),
                "mes": str(month),
                "departamento": "META",
                "operadora": "OP",
                "campo": "CAMPO TEST",
                "produccionkpc": f"{val:,.2f}",
            }
            for val in gas_parts
        ],
    }
    tbl = colombia.Colombia().parse_production(io.BytesIO(json.dumps(bundle).encode("utf-8")))
    assert tbl.num_rows == 1
    days = units.days_in_month(year, month)
    expected_oil = sum(round(v, 2) for v in oil_parts) / days
    expected_gas = units.monthly_volume_to_rate(
        sum(round(v, 2) for v in gas_parts), days, "mcf", "e3m3/d"
    )
    assert tbl.column("oil_prod")[0].as_py() == pytest.approx(expected_oil, rel=1e-4, abs=1e-3)
    assert tbl.column("gas_prod")[0].as_py() == pytest.approx(expected_gas, rel=1e-4, abs=1e-3)
