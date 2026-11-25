"""Tests for Argentina Secretaría de Energía (Capítulo IV) connector."""

from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx2
import pandas as pd
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from ogprod import Store, audit, export_web, schema, sources, units
from ogprod.sources import argentina

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


def test_argentina_metadata_and_adr_0011_defaults() -> None:
    conn = sources.get("argentina")
    assert conn.name == "argentina"
    assert conn.country == "Argentina"
    assert conn.country_code == "AR"
    assert conn.publisher == "Secretaría de Energía"
    assert conn.entity_type is schema.EntityType.FIELD
    assert conn.web_wells is False
    assert conn.search_all_wells is False
    assert "CC BY 4.0" in conn.licence
    assert conn.licence_url == "https://creativecommons.org/licenses/by/4.0/"


def test_parse_capitulo_iv_default_field_rollup_and_injection() -> None:
    table = argentina.Argentina().parse_production(DATA_DIR / "argentina_capitulo_iv_sample.csv")
    df = table.to_pandas().set_index("entity_id")

    assert len(df) == 3
    assert set(df["entity_type"]) == {"field"}
    assert df["oil_prod"].dtype == "float32"
    assert df["gas_prod"].dtype == "float32"

    # March 2024 has 31 days; Loma Campana aggregates 2 Vaca Muerta wells.
    lca = df.loc["LOMA CAMPANA"]
    assert lca["field_id"] == "LOMA CAMPANA"
    assert lca["basin"] == "Neuquina"
    assert lca["region"] == "Neuquén"
    assert lca["environment"] == "onshore"
    assert lca["operator"] == "YPF S.A."
    assert math.isclose(
        float(lca["oil_prod"]), (2500.5 + 1500.0) * units.BBL_PER_M3 / 31.0, rel_tol=1e-4
    )
    assert math.isclose(float(lca["gas_prod"]), (180.2 + 120.0) / 31.0, rel_tol=1e-4)
    assert math.isclose(
        float(lca["water_prod"]), (320.7 + 100.0) * units.BBL_PER_M3 / 31.0, rel_tol=1e-4
    )
    assert math.isclose(float(lca["water_inj"]), 500.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)
    assert pd.isna(lca["gas_inj"])
    assert pd.isna(lca["co2_inj"])

    cgo = df.loc["CERRO DRAGON"]
    assert cgo["basin"] == "Golfo San Jorge"
    assert cgo["region"] == "Chubut"
    assert math.isclose(float(cgo["water_inj"]), 2000.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)
    assert math.isclose(float(cgo["gas_inj"]), 5.0 / 31.0, rel_tol=1e-4)
    assert math.isclose(float(cgo["co2_inj"]), 1.2 / 31.0, rel_tol=1e-4)

    vpl = df.loc["VEGA PLEYADE"]
    assert vpl["basin"] == "Austral"
    assert vpl["region"] == "Estado Nacional"
    assert vpl["environment"] == "offshore"
    assert pd.isna(vpl["water_inj"])


def test_parse_capitulo_iv_local_well_opt_in_and_duplicates(tmp_path: Path) -> None:
    conn = argentina.Argentina()
    table = conn.parse_production(DATA_DIR / "argentina_capitulo_iv_sample.csv", granularity="well")
    df = table.to_pandas().set_index("entity_id")
    assert len(df) == 4
    assert set(df["entity_type"]) == {"well"}
    assert df.loc["130589", "entity_name"] == "YPF.Nq.LACh-15(h)"
    assert df.loc["130589", "field_id"] == "LOMA CAMPANA"

    # Helper function matches granularity="well" and survives blank idpozo + duplicates.
    csv_path = tmp_path / "cap_iv_dirty.csv"
    csv_path.write_text(
        "\ufeffidpozo,sigla,anio,mes,prod_pet,prod_gas,prod_agua,areayacimiento,cuenca,provincia,empresa\n"
        "130589,W-1,2024,2,100.0,10.0,5.0,LOMA CAMPANA,NEUQUINA,Neuquén,YPF S.A.\n"
        "130589,W-1,2024,2,50.0,5.0,2.5,LOMA CAMPANA,NEUQUINA,Neuquén,YPF S.A.\n"
        ",BLANK-ID,2024,2,99.0,9.0,0.0,LOMA CAMPANA,NEUQUINA,Neuquén,YPF S.A.\n"
        "99001,BLANK-YEAR,,2,1.0,1.0,0.0,LOMA CAMPANA,NEUQUINA,Neuquén,YPF S.A.\n",
        encoding="utf-8",
    )
    wells_tbl = argentina.parse_well_production(csv_path)
    wdf = wells_tbl.to_pandas()
    assert len(wdf) == 1
    assert wdf.iloc[0]["entity_id"] == "130589"  # not "130589.0"
    # February 2024 is a leap month (29 days)
    assert math.isclose(
        float(wdf.iloc[0]["oil_prod"]), 150.0 * units.BBL_PER_M3 / 29.0, rel_tol=1e-4
    )


def test_field_fallback_and_dominant_operator(tmp_path: Path) -> None:
    csv_path = tmp_path / "fallback.csv"
    csv_path.write_text(
        "idpozo,anio,mes,prod_pet,prod_gas,areayacimiento,areapermisoconcesion,cuenca,provincia,empresa\n"
        "1,2024,1,10.0,0.0,LOMA CAMPANA,LOMA CAMPANA,NEUQUINA,Neuquén,SMALL OP\n"
        "2,2024,1,900.0,50.0,LOMA CAMPANA,LOMA CAMPANA,NEUQUINA,Neuquén,YPF S.A.\n"
        "3,2024,1,100.0,0.0,SIN YACIMIENTO,BAJADA DEL PALO,NEUQUINA,Neuquén,VISTA\n"
        "4,2024,1,20.0,0.0,,,CUYANA,Mendoza,OP-C\n",
        encoding="utf-8",
    )
    df = argentina.Argentina().parse_production(csv_path).to_pandas().set_index("entity_id")
    assert df.loc["LOMA CAMPANA", "operator"] == "YPF S.A."
    assert "BAJADA DEL PALO" in df.index
    assert "UNASSIGNED" in df.index


def test_rejects_foreign_csv_and_bad_options(tmp_path: Path) -> None:
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a,b\n1,2\n", encoding="utf-8")
    conn = argentina.Argentina()
    with pytest.raises(ValueError, match="Capítulo IV"):
        conn.parse_production(bogus)
    bad_granularity: Any = "basin"
    with pytest.raises(ValueError, match="granularity"):
        conn.parse_production(bogus, granularity=bad_granularity)
    with pytest.raises(ValueError, match="environment"):
        conn.discover_production(environment="subsea")


def test_discover_production_filters_lookalikes_and_deduplicates_years() -> None:
    package_payload = {
        "success": True,
        "result": {
            "resources": [
                {
                    "name": "Producción de Pozos de Gas y Petróleo No Convencional",
                    "format": "CSV",
                    "created": "2019-01-09T13:08:07",
                    "url": "https://example.test/no-conv.csv",
                },
                {
                    "name": (
                        "Producción de Pozos de Gas y Petróleo - 2025 (DDJJ abiertas y cerradas)"
                    ),
                    "format": "CSV",
                    "created": "2026-01-01T00:00:00",
                    "url": "https://example.test/2025-ddjj.csv",
                },
                {
                    "name": "Producción de Pozos de Gas y Petróleo \u2013 2025",
                    "format": "CSV",
                    "created": "2025-02-05T13:06:10",
                    "last_modified": "2026-10-03T08:09:01",
                    "url": "https://example.test/2025-old.csv",
                },
                {
                    "name": "Producción de Pozos de Gas y Petróleo - 2025",
                    "format": "CSV",
                    "created": "2025-04-29T13:05:22",
                    "last_modified": "2026-11-24T08:02:38",
                    "url": "https://example.test/2025-new.csv",
                },
                {
                    "name": "Producción de Pozos de Gas y Petróleo \u2013 2026",
                    "format": "CSV",
                    "created": "2026-02-03T16:31:04",
                    "last_modified": "2026-11-23T10:02:03",
                    "url": "https://example.test/2026.csv",
                },
                {
                    "name": "Producción de Pozos de Gas y Petróleo 2018",
                    "format": "CSV",
                    "created": "2018-05-03T09:32:51",
                    "url": "https://example.test/2018.csv",
                },
            ]
        },
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        del request
        return httpx2.Response(200, json=package_payload)

    conn = argentina.Argentina()
    with httpx2.Client(transport=httpx2.MockTransport(handler)) as client:
        all_files = conn.discover_production(client=client)
        assert [(r.year_min, r.url) for r in all_files] == [
            (2018, "https://example.test/2018.csv"),
            (2025, "https://example.test/2025-new.csv"),
            (2026, "https://example.test/2026.csv"),
        ]

        only_2025 = conn.discover_production(client=client, year=2025)
        assert len(only_2025) == 1
        assert only_2025[0].url == "https://example.test/2025-new.csv"
        assert only_2025[0].key == "capitulo_iv_2025.csv"

        recent = conn.discover_production(client=client, recent=True)
        assert [r.year_min for r in recent] == [2025, 2026]

        with pytest.raises(ValueError, match="no Argentina Capítulo IV CSV for 2020"):
            conn.discover_production(client=client, year=2020)


def test_parse_geo_fields_csv_and_wells_legacy_listado(tmp_path: Path) -> None:
    fields_csv = tmp_path / "yacimientos.csv"
    poly = json.dumps(shapely.geometry.mapping(shapely.box(-69.0, -38.8, -68.8, -38.6)))
    poly_csv = poly.replace('"', '""')
    fields_csv.write_text(
        "yacimiento,cod_yacimiento,cuenca,provincia,empresa,superficie,geojson\n"
        f'LOMA CAMPANA,LCA,NEUQUINA,Neuquén,YPF S.A.,395.0,"{poly_csv}"\n'
        f'SIN YACIMIENTO,XXX,NEUQUINA,Neuquén,YPF S.A.,1.0,"{poly_csv}"\n',
        encoding="utf-8",
    )
    conn = argentina.Argentina()
    fields = conn.parse_geo(schema.GeoKind.FIELDS, [fields_csv]).set_index("field_id")
    assert list(fields.index) == ["LOMA CAMPANA"]
    assert fields.loc["LOMA CAMPANA", "field_code"] == "LCA"
    assert fields.loc["LOMA CAMPANA", "basin"] == "Neuquina"
    assert fields.loc["LOMA CAMPANA", "region"] == "Neuquén"
    assert math.isclose(float(fields.loc["LOMA CAMPANA", "area_km2"]), 395.0, rel_tol=1e-4)

    # Legacy Listado de pozos CSV with coordenadax/coordenaday and bad/zero coordinates.
    listado_csv = tmp_path / "listado.csv"
    listado_csv.write_text(
        "idpozo,sigla,idempresa,areayacimiento,cuenca,provincia,clasificacion,coordenadax,coordenaday\n"
        "50136,YPF.NQ.LCa-1(h),YPF,LOMA CAMPANA,NEUQUINA,Neuquén,EXPLOTACION,-68.918,-38.665\n"
        "40211,PAE.CG.EC-7,PAE,CERRO DRAGON,GOLFO SAN JORGE,Chubut,EXPLOTACION,0,0\n"
        "40212,BAD-COORD,PAE,CERRO DRAGON,GOLFO SAN JORGE,Chubut,EXPLOTACION,999,-38.0\n"
        ",BLANK-ID,YPF,LOMA CAMPANA,NEUQUINA,Neuquén,EXPLOTACION,-68.9,-38.6\n",
        encoding="utf-8",
    )
    wells = conn.parse_geo(schema.GeoKind.WELLS, [listado_csv]).set_index("well_id")
    assert set(wells.index) == {"50136", "40211", "40212"}
    assert wells.loc["50136"].geometry.x == pytest.approx(-68.918)
    assert wells.loc["50136"].geometry.y == pytest.approx(-38.665)
    assert wells.loc["40211"].geometry is None
    assert wells.loc["40212"].geometry is None

    with pytest.raises(NotImplementedError):
        conn.parse_geo(schema.GeoKind.FACILITIES, [])


def test_argentina_end_to_end_mock_ingest_audit_and_web_export(tmp_path: Path) -> None:
    zips_dir = tmp_path / "mock_zips"
    zips_dir.mkdir()
    fields_zip = _write_shp_zip(
        gpd.GeoDataFrame(
            {
                "YACIMIENTO": ["LOMA CAMPANA", "CERRO DRAGON", "VEGA PLEYADE"],
                "COD_YACIMI": ["LCA", "CGO", "VPL"],
                "CUENCA": ["NEUQUINA", "GOLFO SAN JORGE", "AUSTRAL"],
                "PROVINCIA": ["Neuquén", "Chubut", "Estado Nacional"],
                "EMPRESA_IN": ["YPF S.A.", "PAN AMERICAN ENERGY", "TOTAL AUSTRAL S.A."],
                "SUPERFICIE": [395.0, 2200.0, 150.0],
            },
            geometry=[
                shapely.box(-69.0, -38.8, -68.8, -38.6),
                shapely.box(-68.2, -45.9, -67.8, -45.6),
                shapely.box(-68.1, -53.5, -67.8, -53.2),
            ],
            crs="EPSG:4326",
        ),
        zips_dir / "yacimientos.zip",
        "yacimientos",
    )

    prod_pkg = {
        "success": True,
        "result": {
            "resources": [
                {
                    "name": "Producción de Pozos de Gas y Petróleo - 2024",
                    "format": "CSV",
                    "created": "2025-04-29T11:37:18",
                    "url": "https://example.test/cap_iv_2024.csv",
                },
                {
                    "name": "Capítulo IV - Pozos",
                    "format": "CSV",
                    "created": "2017-05-04T14:14:07",
                    "url": "https://example.test/capitulo-iv-pozos.csv",
                },
            ]
        },
    }
    fields_pkg = {
        "success": True,
        "result": {
            "resources": [
                {
                    "name": "Producción Hidrocarburos Yacimientos",
                    "format": "SHP",
                    "created": "2017-05-04T22:23:05",
                    "url": "https://example.test/yacimientos.zip",
                }
            ]
        },
    }

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if "produccion-de-petroleo-y-gas-por-pozo" in url:
            return httpx2.Response(200, json=prod_pkg)
        if "produccion-hidrocarburos-yacimientos" in url:
            return httpx2.Response(200, json=fields_pkg)
        if url.endswith("cap_iv_2024.csv"):
            return httpx2.Response(
                200, content=(DATA_DIR / "argentina_capitulo_iv_sample.csv").read_bytes()
            )
        if url.endswith("capitulo-iv-pozos.csv"):
            return httpx2.Response(
                200, content=(DATA_DIR / "argentina_pozos_sample.csv").read_bytes()
            )
        if url.endswith("yacimientos.zip"):
            return httpx2.Response(200, content=fields_zip.read_bytes())
        return httpx2.Response(404)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    data_dir = tmp_path / "data"
    db = Store(data_dir)
    summary = db.ingest("argentina", origin="publisher", year=2024, client=client)

    assert summary.production_rows == 3
    assert summary.geo_counts == {"fields": 3, "wells": 3}

    wells = db.wells(source="argentina").set_index("well_id")
    assert wells.loc["130589", "spud_date"] == "2021-04-10"
    assert wells.loc["130589", "completion_date"] == "2021-07-22"
    assert wells.loc["130589", "lift_method"] == "Surgencia Natural"
    assert wells.loc["200100", "environment"] == "offshore"

    report = audit.audit_store(data_dir, source="argentina", stale_months=100)
    assert report.ok, report.format_text()
    assert not report.warnings, report.format_text()

    # ADR 0011: web_wells = False omits Argentina wells from Layer-2 search & tiles.
    web_out = tmp_path / "web"
    export_web.export_web(data_dir=data_dir, out_dir=web_out, skip_tiles=True)
    search = json.loads((web_out / "search" / "index.json").read_text("utf-8"))
    assert any(item["t"] == "field" and item["s"] == "argentina" for item in search)
    assert not any(item["t"] == "well" and item["s"] == "argentina" for item in search)
    assert export_web._well_tile_features(db.wells(source="argentina"), {}) == []


@settings(max_examples=40, deadline=None)
@given(
    year=st.integers(min_value=2006, max_value=2026),
    month=st.integers(min_value=1, max_value=12),
    wells=st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=999_999),
            st.sampled_from(["LOMA CAMPANA", "CERRO DRAGON", "VEGA PLEYADE"]),
            st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=1e4, allow_nan=False, allow_infinity=False),
            st.floats(min_value=0.0, max_value=1e5, allow_nan=False, allow_infinity=False),
        ),
        min_size=1,
        max_size=12,
        unique_by=lambda row: row[0],
    ),
)
def test_field_rollup_conserves_total_well_rates(
    year: int, month: int, wells: list[tuple[int, str, float, float, float]]
) -> None:
    lines = ["idpozo,anio,mes,prod_pet,prod_gas,prod_agua,areayacimiento,cuenca,provincia,empresa"]
    for wid, fld, oil, gas, water in wells:
        lines.append(f"{wid},{year},{month},{oil},{gas},{water},{fld},NEUQUINA,Neuquén,YPF S.A.")
    csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")

    conn = argentina.Argentina()
    field_df = conn.parse_production(io.BytesIO(csv_bytes), granularity="field").to_pandas()
    well_df = conn.parse_production(io.BytesIO(csv_bytes), granularity="well").to_pandas()

    days = units.days_in_month(year, month)
    expected_oil = sum(w[2] for w in wells) * units.BBL_PER_M3 / days
    expected_gas = sum(w[3] for w in wells) / days
    expected_water = sum(w[4] for w in wells) * units.BBL_PER_M3 / days

    assert math.isclose(
        float(field_df["oil_prod"].sum()),
        float(well_df["oil_prod"].sum()),
        rel_tol=1e-4,
        abs_tol=1e-2,
    )
    assert math.isclose(float(field_df["oil_prod"].sum()), expected_oil, rel_tol=1e-4, abs_tol=1e-2)
    assert math.isclose(float(field_df["gas_prod"].sum()), expected_gas, rel_tol=1e-4, abs_tol=1e-2)
    assert math.isclose(
        float(field_df["water_prod"].sum()), expected_water, rel_tol=1e-4, abs_tol=1e-2
    )


@given(n=st.integers(min_value=1, max_value=10_000_000))
def test_clean_int_id_strips_float_suffix(n: int) -> None:
    s = pd.Series([f"{n}.0", float(n), str(n), None])
    cleaned = argentina._clean_int_id(s)
    assert cleaned.iloc[0] == str(n)
    assert cleaned.iloc[1] == str(n)
    assert cleaned.iloc[2] == str(n)
    assert pd.isna(cleaned.iloc[3])
