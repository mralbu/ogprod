"""Tests for Mexico SENER/CNIH SIH production and Mapa Hidrocarburos spatial connector."""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import geopandas as gpd
import httpx2
import pandas as pd
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st

from ogprod import Store, schema, units
from ogprod.sources import mexico

DATA_DIR = Path(__file__).parent / "data"


def _write_shp_zip(gdf: gpd.GeoDataFrame, dest_zip: Path, stem: str) -> Path:
    shp_dir = dest_zip.parent / f"_{stem}"
    shp_dir.mkdir(parents=True, exist_ok=True)
    shp_path = shp_dir / f"{stem}.shp"
    gdf.to_file(shp_path)
    with zipfile.ZipFile(dest_zip, "w") as archive:
        for member in shp_dir.iterdir():
            archive.write(member, arcname=member.name)
    return dest_zip


def test_parse_mexico_campos_production_units() -> None:
    table = mexico.Mexico().parse_production(DATA_DIR / "mexico_campos_sample.csv")
    df = table.to_pandas().set_index("entity_id")

    assert len(df) == 2
    assert df["oil_prod"].dtype == "float32"
    assert df["gas_prod"].dtype == "float32"

    maloob = df.loc["MALOOB"]
    assert maloob["entity_type"] == "field"
    assert maloob["environment"] == "offshore"
    assert math.isclose(float(maloob["oil_prod"]), 250_500.0, rel_tol=1e-4)
    assert math.isclose(float(maloob["water_prod"]), 40_000.0, rel_tol=1e-4)
    expected_gas = 100.0 * units.volume_factor("mmcf", "e3m3")
    assert math.isclose(float(maloob["gas_prod"]), expected_gas, rel_tol=1e-4)

    quesqui = df.loc["QUESQUI"]
    assert quesqui["environment"] == "onshore"
    assert math.isclose(float(quesqui["condensate_prod"]), 100_000.0, rel_tol=1e-4)
    assert math.isclose(float(quesqui["gas_prod"]), expected_gas, rel_tol=1e-4)


def test_parse_mexico_pozos_csv_and_zip(tmp_path: Path) -> None:
    connector = mexico.Mexico()
    table = connector.parse_production(DATA_DIR / "mexico_pozos_sample.csv")
    df = table.to_pandas()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["entity_type"] == "well"
    assert row["entity_id"] == "1000023609"
    assert row["entity_name"] == "Poza Rica-2"
    assert row["basin"] == "Tampico-Misantla"
    assert math.isclose(float(row["oil_prod"]), 1200.0, rel_tol=1e-4)
    assert math.isclose(float(row["condensate_prod"]), 300.0, rel_tol=1e-4)

    zip_path = tmp_path / "PRODUCCION_POZOS.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.write(DATA_DIR / "mexico_pozos_sample.csv", arcname="POZOS_COMPILADO.csv")
    from_zip = connector.parse_production(zip_path)
    assert from_zip.num_rows == 1


def test_mexico_rejects_foreign_csv() -> None:
    with pytest.raises(ValueError, match="not a Mexico SIH"):
        mexico.Mexico().parse_production(DATA_DIR / "anp_mar_2026_sample.csv")


def test_mexico_end_to_end_mock_ingest(tmp_path: Path) -> None:
    zips_dir = tmp_path / "mock_zips"
    zips_dir.mkdir()

    fields_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {
                "nombre": ["MALOOB"],
                "superficie": ["45.2"],
                "ubicacin": ["Aguas someras"],
                "unused_1p": ["999"],
            },
            geometry=[shapely.box(-92.3, 19.5, -92.1, 19.7)],
            crs="EPSG:4326",
        ),
        zips_dir / "campos.zip",
        "campos",
    )
    provinces_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {
                "nombre": ["CUENCAS DEL SURESTE", "BURGOS"],
                "situacin": ["PRODUCTORAS", "PRODUCTORAS"],
            },
            geometry=[
                shapely.box(-94.0, 18.0, -91.0, 21.0),
                shapely.box(-99.5, 24.5, -97.0, 26.5),
            ],
            crs="EPSG:4326",
        ),
        zips_dir / "provincias_petroleras.zip",
        "provincias_petroleras",
    )
    fac_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {
                "nombre_de_": ["PLATAFORMA KU-A"],
                "tipo": ["PLATAFORMA MARINA"],
                "estado_act": ["OPERANDO"],
                "fecha_de_c": ["2005-06-15"],
            },
            geometry=[shapely.Point(-92.2, 19.6)],
            crs="EPSG:4326",
        ),
        zips_dir / "instalaciones.zip",
        "instalaciones",
    )
    well_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {
                "pozo": ["CHINWOL-1EXP"],
                "campo": ["CHINWOL"],
                "entidad": ["AGUAS TERRITORIALES"],
                "ubicacin": ["AGUAS PROFUNDAS"],
                "estado_act": ["TAPONAMIENTO PERMANENTE"],
                "fecha_inic": ["29-FEB-2020"],
                "fecha_fin_": ["21-ABR-2020"],
            },
            geometry=[shapely.Point(-95.0, 21.0)],
            crs="EPSG:4326",
        ),
        zips_dir / "pozos.zip",
        "pozos",
    )

    export_targets = {
        "cmp_cmp_rsv": "tmp/campos.zip",
        "geo_petro_prov": "tmp/provincias_petroleras.zip",
        "v_iftep_instalaciones": "tmp/instalaciones.zip",
    }
    zip_files = {
        "provincias_petroleras.zip": provinces_zip,
        "campos.zip": fields_zip,
        "instalaciones.zip": fac_zip,
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("PRODUCCION_CAMPOS.csv"):
            return httpx2.Response(
                200, content=(DATA_DIR / "mexico_campos_sample.csv").read_bytes()
            )
        if url.endswith("export.php"):
            body = request.content.decode("utf-8")
            path = next((p for k, p in export_targets.items() if k in body), "tmp/pozos.zip")
            return httpx2.Response(200, json={"filePath": path})
        if "getFile.php" in url:
            src = next((z for k, z in zip_files.items() if k in url), well_zip)
            return httpx2.Response(200, content=src.read_bytes())
        return httpx2.Response(404)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    db = Store(tmp_path / "data")
    summary = db.ingest("mexico", origin="publisher", client=client)

    assert summary.production_rows == 2
    assert summary.geo_counts == {"fields": 1, "facilities": 1, "wells": 1}
    assert (tmp_path / "data" / "store" / "geo" / "fields" / "mexico.parquet").exists()

    fields = db.fields(source="mexico").set_index("field_id")
    assert fields.loc["MALOOB", "basin"] == "Cuencas del Sureste"
    assert fields.loc["MALOOB", "environment"] == "offshore"
    assert math.isclose(float(fields.loc["MALOOB", "area_km2"]), 45.2, rel_tol=1e-4)

    fac = db.facilities(source="mexico").set_index("facility_id")
    assert fac.loc["PLATAFORMA KU-A", "start_year"] == 2005

    wells = db.wells(source="mexico").set_index("well_id")
    assert wells.loc["CHINWOL-1EXP", "region"] == "Aguas Territoriales"
    assert wells.loc["CHINWOL-1EXP", "spud_date"] == "2020-02-29"
    assert wells.loc["CHINWOL-1EXP", "completion_date"] == "2020-04-21"
    assert wells.loc["CHINWOL-1EXP", "field_id"] == "CHINWOL"

    manifest = json.loads((tmp_path / "data" / "store" / "manifest.json").read_text("utf-8"))
    mx_meta = manifest["sources"]["mexico"]
    assert mx_meta["country"] == "Mexico"
    assert mx_meta["country_code"] == "MX"
    assert mx_meta["publisher"] == "SENER / CNIH"
    assert "PRODUCCION_CAMPOS.csv" in mx_meta["files"]
    assert schema.GeoKind.FIELDS.value in summary.geo_counts


def test_mexico_fields_parse_without_provinces_and_reversed_path_order(tmp_path: Path) -> None:
    fields_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {"nombre": ["IXACHI"], "ubicacin": ["Terrestre"], "superficie": ["12.0"]},
            geometry=[shapely.box(-96.2, 18.6, -96.0, 18.8)],
            crs="EPSG:4326",
        ),
        tmp_path / "campos.zip",
        "campos",
    )
    provinces_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {"nombre": ["VERACRUZ"]},
            geometry=[shapely.box(-97.0, 18.0, -95.5, 19.5)],
            crs="EPSG:4326",
        ),
        tmp_path / "provincias_petroleras.zip",
        "provincias_petroleras",
    )

    connector = mexico.Mexico()
    without_prov = connector.parse_geo(schema.GeoKind.FIELDS, [fields_zip]).set_index("field_id")
    assert without_prov["basin"].isna().loc["IXACHI"]

    reversed_order = connector.parse_geo(
        schema.GeoKind.FIELDS, [provinces_zip, fields_zip]
    ).set_index("field_id")
    assert reversed_order.loc["IXACHI", "basin"] == "Veracruz"


def test_mexico_basin_and_region_name_normalization() -> None:
    basins = mexico._normalize_mexico_basin(
        pd.Series(
            [
                "CUENCAS DEL SURESTE",
                "SABINAS - BURRO - PICACHOS",
                "GOLFO DE MEXICO PROFUNDO",
                "CINTURON PLEGADO DE CHIAPAS",
                "TAMPICO-MISANTLA",
                "NO DISPONIBLE",
            ]
        )
    )
    assert basins.iloc[:5].tolist() == [
        "Cuencas del Sureste",
        "Sabinas-Burro-Picachos",
        "Golfo de México Profundo",
        "Cinturón Plegado de Chiapas",
        "Tampico-Misantla",
    ]
    assert pd.isna(basins.iloc[5])

    regions = mexico._normalize_mexico_region(
        pd.Series(
            [
                "VERACRUZ DE IGNACIO DE LA LLAVE",
                "NUEVO LEON",
                "SAN LUIS POTOSI",
                "CIUDAD DE MEXICO",
                "AGUAS TERRITORIALES",
                "YUCATAN",
            ]
        )
    )
    assert regions.tolist() == [
        "Veracruz de Ignacio de la Llave",
        "Nuevo León",
        "San Luis Potosí",
        "Ciudad de México",
        "Aguas Territoriales",
        "Yucatán",
    ]


@given(
    raw=st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzáéíóúÁÉÍÓÚñÑ -",
        min_size=1,
        max_size=40,
    )
)
def test_mexico_spanish_label_normalization_is_idempotent(raw: str) -> None:
    once = mexico._normalize_mexico_basin(pd.Series([raw]))
    twice = mexico._normalize_mexico_basin(once)
    assert once.equals(twice)

    r_once = mexico._normalize_mexico_region(pd.Series([raw]))
    r_twice = mexico._normalize_mexico_region(r_once)
    assert r_once.equals(r_twice)


def test_mexico_field_id_normalization_and_bad_fecha_rejected() -> None:
    import io

    csv_ok = (
        "FECHA,CAMPO_OFICIAL,CAMPO_SIH,UBICACION,PETROLEO_MBD,GAS_ASOC_MMPCD\n"
        "01/2025,Tupilco Profundo,TUPILCOPROFUNDO,Terrestre,50.0,10.0\n"
        "01/2025,Chiapas-Copano,CHIAPAS COPANO,Terrestre,20.0,5.0\n"
    )
    df = mexico.Mexico().parse_production(io.BytesIO(csv_ok.encode("utf-8"))).to_pandas()
    assert set(df["field_id"]) == {"TUPILCO PROFUNDO", "CHIAPAS COPANO"}

    csv_bad = "FECHA,CAMPO_OFICIAL,CAMPO_SIH,UBICACION,PETROLEO_MBD\n2025-01,Maloob,MALOOB,Mar,10\n"
    with pytest.raises(ValueError, match="unparseable FECHA"):
        mexico.Mexico().parse_production(io.BytesIO(csv_bad.encode("utf-8")))


def test_mexico_wells_cleans_no_disponible_sentinel(tmp_path: Path) -> None:
    well_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {
                "pozo": ["EXPL-1", "PROD-1"],
                "campo": ["NO DISPONIBLE", "Tupilco-Profundo"],
                "entidad": ["TABASCO", "SAN LUIS POTOSI"],
                "ubicacin": ["TERRESTRE", "TERRESTRE"],
                "estado_act": ["CERRADO", "PRODUCTOR"],
                "fecha_inic": ["01-ENE-2020", "01-FEB-2020"],
                "fecha_fin_": ["01-MAR-2020", "01-ABR-2020"],
            },
            geometry=[shapely.Point(-93.1, 18.1), shapely.Point(-93.2, 18.2)],
            crs="EPSG:4326",
        ),
        tmp_path / "pozos.zip",
        "pozos",
    )
    wells = mexico.Mexico().parse_geo(schema.GeoKind.WELLS, [well_zip]).set_index("well_id")
    assert wells["field_id"].isna().loc["EXPL-1"]
    assert wells["field_name"].isna().loc["EXPL-1"]
    assert wells.loc["EXPL-1", "region"] == "Tabasco"
    assert wells.loc["PROD-1", "field_id"] == "TUPILCO PROFUNDO"
    assert wells.loc["PROD-1", "region"] == "San Luis Potosí"
