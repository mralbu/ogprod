"""Tests for the Brazil (ANP) connector (offline parsing, DMS coords, mock HTTP ingest)."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path

import httpx2
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ogprod import Store, schema, units
from ogprod.sources import brazil

DATA_DIR = Path(__file__).parent / "data"

ANP_FIELDS_GEOJSON = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4674"}},
    "features": [
        {
            "type": "Feature",
            "properties": {
                "COD_CAMPO": 999,
                "NOM_CAMPO": "BÚZIOS",
                "NOM_BACIA": "Santos",
                "OPERADOR_C": "PETROBRAS",
                "AMBIENTE": "M",
                "ETAPA": "Produção",
                "FLUIDO_PRI": "ÓLEO",
                "MED_LAMINA": 1900,
                "AREA": 416.5,
                "DAT_DESCOB": "2010-05-01",
                "DAT_INICIO": "2018-04-20",
                "UNUSED_PROP": "should-be-dropped",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [-42.7, -24.7],
                        [-42.4, -24.7],
                        [-42.4, -24.5],
                        [-42.7, -24.5],
                        [-42.7, -24.7],
                    ]
                ],
            },
        }
    ],
}

ANP_FIELDS_ACTIVE_CSV = (
    "CODIGO_CAMPO,CAMPO,SIGLA_CAMPO,BACIA,ESTADO,OPERADOR,"
    "AMBIENTE,SITUACAO_CAMPO,DATA_INICIO_PRODUCAO\n"
    "999,BÚZIOS,BUZ,Santos,Rio de Janeiro,PETROBRAS,Mar,Produção,20/04/2018\n"
)

ANP_FIELDS_RELINQ_CSV = (
    "CODIGO_CAMPO,CAMPO,SIGLA_CAMPO,BACIA,ESTADO,OPERADOR,"
    "AMBIENTE,SITUACAO_CAMPO,DATA_INICIO_PRODUCAO\n"
    "1,CANDEIAS HIST,CDH,Recôncavo,Bahia,PETROBRAS,Terra,Devolvido,01/01/1941\n"
)


@given(
    sign=st.sampled_from([-1, 1]),
    deg=st.integers(min_value=0, max_value=89),
    minutes=st.integers(min_value=0, max_value=59),
    seconds=st.floats(min_value=0.0, max_value=59.999, allow_nan=False, allow_infinity=False),
)
def test_parse_dms_coord_exact_for_all_hemispheres(
    sign: int, deg: int, minutes: int, seconds: float
) -> None:
    prefix = "-" if sign < 0 else ""
    text = f"{prefix}{deg:02d}:{minutes:02d}:{seconds:06.3f}".replace(".", ",")
    expected = sign * (deg + minutes / 60.0 + seconds / 3600.0)
    parsed = brazil.parse_dms_coord(text)
    assert parsed is not None
    assert math.isclose(parsed, expected, rel_tol=1e-6, abs_tol=1e-6)


def test_parse_brazil_production_and_injection_rates() -> None:
    connector = brazil.Brazil()
    table = connector.parse_production(DATA_DIR / "anp_mar_2026_sample.csv")
    df = table.to_pandas().set_index("entity_id")

    assert len(df) == 2
    assert df["oil_prod"].dtype == "float32"
    assert df["gas_prod"].dtype == "float32"
    assert df["water_inj"].dtype == "float32"

    # Jan 2026 has 31 days. 15898.73 m3 == 100,000 bbl -> 100,000 / 31 bbl/d.
    buz = df.loc["7-BUZ-1-RJS"]
    assert buz["source"] == "brazil"
    assert buz["field_id"] == "BUZIOS"
    assert buz["facility_id"] == "FPSO ALMIRANTE BARROSO"
    assert buz["environment"] == "offshore"
    assert math.isclose(float(buz["oil_prod"]), 15898.73 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    # 3100 e3m3 / 31 days == 100.0 e3m3/d
    assert math.isclose(float(buz["gas_prod"]), 100.0, rel_tol=1e-4)
    # water_inj = (1589.87 + 317.97) m3 * BBL_PER_M3 / 31
    assert math.isclose(
        float(buz["water_inj"]), (1589.87 + 317.97) * units.BBL_PER_M3 / 31, rel_tol=1e-4
    )
    assert math.isclose(float(buz["gas_inj"]), 620.0 / 31, rel_tol=1e-4)
    assert math.isclose(float(buz["co2_inj"]), 310.0 / 31, rel_tol=1e-4)

    mnt = df.loc["7-MNT-1-BAS"]
    assert math.isclose(float(mnt["condensate_prod"]), 158.99 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(mnt["gas_prod"]), 200.0, rel_tol=1e-4)


def test_parse_brazil_merges_state_cross_join_and_warns_gas_gap() -> None:
    connector = brazil.Brazil()
    with pytest.warns(UserWarning, match="2003-07..2005-06"):
        table = connector.parse_production(DATA_DIR / "anp_2004_duplicate_sample.csv")
    df = table.to_pandas()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["region"] == "Bahia"  # matches -BA well suffix
    assert row["facility_id"] == ""  # Não Informado normalized to empty key
    # Oil kept once (not doubled), gas recovered from the other copy.
    assert math.isclose(float(row["oil_prod"]), 158.99 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(row["gas_prod"]), 1.0, rel_tol=1e-4)


def test_parse_brazil_rejects_conflicting_duplicate_rows() -> None:
    raw_text = (DATA_DIR / "anp_2004_duplicate_sample.csv").read_text(encoding="utf-8")
    bad_csv = raw_text.replace('"158,99","0,00","0,000"', '"999,00","0,00","0,000"')
    with pytest.raises(schema.SchemaError, match="conflicting duplicate"):
        brazil.Brazil().parse_production(io.BytesIO(bad_csv.encode("utf-8")))


def test_parse_brazil_prefers_state_matching_row_on_small_rounding_diff() -> None:
    # Mirrors real 2003-12 FAZENDA BELÉM cross-join (Bahia 228.6 m³ vs Ceará 230 m³).
    csv_text = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,"
        "Produção de Óleo (m³),Produção de Condensado (m³),"
        "Produção de Gás Associado (Mm³),Produção de Gás Não Associado (Mm³),"
        "Produção de Água (m³)\n"
        "2003,12/2003,Bahia,Recôncavo,FAZENDA BELÉM,7-FZB-379-CE,Terra,Não Informado,"
        '"228,6",0,"1,143",0,"299,2"\n'
        "2003,12/2003,Ceará,Potiguar,FAZENDA BELÉM,7-FZB-379-CE,Terra,Não Informado,"
        '230,0,0,0,"299,2"\n'
    )
    with pytest.warns(UserWarning, match="2003-07..2005-06"):
        table = brazil.Brazil().parse_production(io.BytesIO(csv_text.encode("utf-8")))
    df = table.to_pandas()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["region"] == "Ceará"
    assert math.isclose(float(row["oil_prod"]), 230.0 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(row["gas_prod"]), 1.143 / 31, rel_tol=1e-4)


def test_parse_brazil_unwraps_outer_quoted_2019_2021_rows() -> None:
    csv_text = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,"
        "Produção de Óleo (m³),Produção de Condensado (m³),"
        "Produção de Gás Associado (Mm³),Produção de Gás Não Associado (Mm³),"
        "Produção de Água (m³)\n"
        '"2020,01/2020,Rio de Janeiro,Santos,BUZIOS,7-BUZ-1-RJS,Mar,FPSO A,'
        '""15898,73"",0,""3100,0"",0,""500,5"""\n'
        "2020,01/2020,Alagoas,Alagoas,PARU,4-ALS-39-AL,Mar,Não Informado,0,0,0,0,0\n"
    )
    table = brazil.Brazil().parse_production(io.BytesIO(csv_text.encode("utf-8")))
    df = table.to_pandas().set_index("entity_id")
    assert len(df) == 2
    buz = df.loc["7-BUZ-1-RJS"]
    assert math.isclose(float(buz["oil_prod"]), 15898.73 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(buz["gas_prod"]), 100.0, rel_tol=1e-4)


def test_brazil_discover_preserves_year_subdir_in_key() -> None:
    html = (
        '<a href="https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/'
        "arquivos-fase-de-desenvolvimento-e-producao/"
        'arquivos-producao-de-petroleo-e-gas-natural-nacional/pt/2022/producao-terra-3-trim.csv">22</a>'
        '<a href="https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/'
        "arquivos-fase-de-desenvolvimento-e-producao/"
        'arquivos-producao-de-petroleo-e-gas-natural-nacional/pt/2023/producao-terra-3-trim.csv">23</a>'
    )
    client = httpx2.Client(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, text=html))
    )
    remotes = brazil.Brazil().discover_production(client=client)
    keys = [r.key for r in remotes]
    assert keys == ["pt/2022/producao-terra-3-trim.csv", "pt/2023/producao-terra-3-trim.csv"]


def test_brazil_end_to_end_mock_ingest_and_lean_storage(tmp_path: Path) -> None:
    portal_html = (
        '<a href="https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/'
        "arquivos-fase-de-desenvolvimento-e-producao/"
        'arquivos-producao-de-petroleo-e-gas-natural-nacional/pm/producao-mar-2026.csv">2026</a>'
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("fase-de-desenvolvimento-e-producao"):
            return httpx2.Response(200, text=portal_html)
        if url.endswith("producao-mar-2026.csv"):
            return httpx2.Response(200, content=(DATA_DIR / "anp_mar_2026_sample.csv").read_bytes())
        if url.endswith("dados-abertos-plataformas-operacao.csv"):
            return httpx2.Response(200, content=(DATA_DIR / "anp_lpo_sample.csv").read_bytes())
        if "CAMPOS_PRODUCAO_SIRGAS" in url:
            return httpx2.Response(200, json=ANP_FIELDS_GEOJSON)
        if url.endswith("extracao-campo.csv"):
            return httpx2.Response(200, content=ANP_FIELDS_ACTIVE_CSV.encode("utf-8-sig"))
        if url.endswith("extracao-campo-devolvido.csv"):
            return httpx2.Response(200, content=ANP_FIELDS_RELINQ_CSV.encode("utf-8-sig"))
        if url.endswith("situacao-pocos-1939-2026.csv"):
            return httpx2.Response(200, content=(DATA_DIR / "anp_wells_sample.csv").read_bytes())
        return httpx2.Response(404)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    db = Store(tmp_path)
    summary = db.ingest("brazil", year=2026, origin="publisher", client=client)

    assert summary.files_ingested == 1
    assert summary.production_rows == 2
    assert summary.geo_counts == {"fields": 2, "facilities": 1, "wells": 3}
    # Lean default: raw/ directory is NOT kept on disk.
    assert not (tmp_path / "raw").exists()
    # Clean country-named GeoParquet files.
    assert (tmp_path / "store" / "geo" / "fields" / "brazil.parquet").exists()
    assert (tmp_path / "store" / "geo" / "facilities" / "brazil.parquet").exists()
    assert (tmp_path / "store" / "geo" / "wells" / "brazil.parquet").exists()

    # Verify joins across production, facilities, fields, and wells.
    fac = db.facilities(source="brazil")
    assert fac.loc[0, "facility_id"] == "FPSO ALMIRANTE BARROSO"
    assert math.isclose(float(fac.loc[0, "gas_capacity_e3m3d"]), 6291.0, rel_tol=1e-4)

    wells = db.wells(source="brazil").set_index("well_id")
    assert math.isclose(wells.loc["1-AP-1-AP"].geometry.y, -0.5, rel_tol=1e-6)
    assert wells.loc["1-NO-COORD-BA"].geometry is None

    fields = db.fields(source="brazil").set_index("field_id")
    assert fields.loc["BUZIOS", "region"] == "Rio de Janeiro"
    assert fields.loc["CANDEIAS HIST"].geometry is None

    # Verify manifest recorded SHA-256, publisher attribution, and units.
    manifest = json.loads((tmp_path / "store" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["units"]["oil_prod"] == "bbl/d"
    assert manifest["units"]["gas_prod"] == "e3m3/d"
    br_meta = manifest["sources"]["brazil"]
    assert br_meta["country"] == "Brazil"
    assert br_meta["country_code"] == "BR"
    assert br_meta["publisher"] == "ANP"
    assert "pm/producao-mar-2026.csv" in br_meta["files"]
