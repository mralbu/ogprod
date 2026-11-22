"""Tests for Mexico SENER/CNIH SIH production and Mapa Hidrocarburos spatial connector."""

from __future__ import annotations

import json
import math
import zipfile
from pathlib import Path

import geopandas as gpd
import httpx
import pytest
import shapely

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
    # 100 MMpcd -> e3m3/d
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
    assert row["basin"] == "TAMPICO-MISANTLA"
    assert math.isclose(float(row["oil_prod"]), 1200.0, rel_tol=1e-4)
    assert math.isclose(float(row["condensate_prod"]), 300.0, rel_tol=1e-4)

    # Also works directly on a ZIP containing POZOS_COMPILADO.csv.
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

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("PRODUCCION_CAMPOS.csv"):
            return httpx.Response(200, content=(DATA_DIR / "mexico_campos_sample.csv").read_bytes())
        if url.endswith("export.php"):
            body = request.content.decode("utf-8")
            if "cmp_cmp_rsv" in body:
                return httpx.Response(200, json={"filePath": "tmp/campos.zip"})
            if "v_iftep_instalaciones" in body:
                return httpx.Response(200, json={"filePath": "tmp/instalaciones.zip"})
            return httpx.Response(200, json={"filePath": "tmp/pozos.zip"})
        if "getFile.php" in url:
            if "campos.zip" in url:
                return httpx.Response(200, content=fields_zip.read_bytes())
            if "instalaciones.zip" in url:
                return httpx.Response(200, content=fac_zip.read_bytes())
            return httpx.Response(200, content=well_zip.read_bytes())
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    db = Store(tmp_path / "data")
    summary = db.ingest("mexico", client=client)

    assert summary.production_rows == 2
    assert summary.geo_counts == {"fields": 1, "facilities": 1, "wells": 1}

    fields = db.fields(source="mexico").set_index("field_id")
    assert fields.loc["MALOOB", "environment"] == "offshore"
    assert math.isclose(float(fields.loc["MALOOB", "area_km2"]), 45.2, rel_tol=1e-4)

    fac = db.facilities(source="mexico").set_index("facility_id")
    assert fac.loc["PLATAFORMA KU-A", "start_year"] == 2005

    wells = db.wells(source="mexico").set_index("well_id")
    assert wells.loc["CHINWOL-1EXP", "spud_date"] == "2020-02-29"
    assert wells.loc["CHINWOL-1EXP", "completion_date"] == "2020-04-21"
    assert wells.loc["CHINWOL-1EXP", "field_id"] == "CHINWOL"

    manifest = json.loads((tmp_path / "data" / "store" / "manifest.json").read_text("utf-8"))
    assert "PRODUCCION_CAMPOS.csv" in manifest["sources"]["mexico"]["files"]
    assert schema.GeoKind.FIELDS.value in summary.geo_counts
