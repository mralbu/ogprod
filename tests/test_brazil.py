"""Tests for the Brazil (ANP) connector (offline parsing, DMS coords, mock HTTP ingest)."""

from __future__ import annotations

import calendar
import io
import json
import math
from datetime import date
from pathlib import Path

import httpx2
import pytest
from hypothesis import example, given
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

    buz = df.loc["7-BUZ-1-RJS"]
    assert buz["source"] == "brazil"
    assert buz["field_id"] == "BUZIOS"
    assert buz["facility_id"] == "FPSO ALMIRANTE BARROSO"
    assert buz["environment"] == "offshore"
    assert math.isclose(float(buz["oil_prod"]), 15898.73 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(buz["gas_prod"]), 3_100.0 / 31, rel_tol=1e-4)
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
    assert row["region"] == "Bahia"
    assert row["facility_id"] == ""
    assert math.isclose(float(row["oil_prod"]), 158.99 * units.BBL_PER_M3 / 31, rel_tol=1e-4)
    assert math.isclose(float(row["gas_prod"]), 1.0, rel_tol=1e-4)


def test_parse_brazil_rejects_conflicting_duplicate_rows() -> None:
    raw_text = (DATA_DIR / "anp_2004_duplicate_sample.csv").read_text(encoding="utf-8")
    bad_csv = raw_text.replace('"158,99","0,00","0,000"', '"999,00","0,00","0,000"')
    with pytest.raises(schema.SchemaError, match="conflicting duplicate"):
        brazil.Brazil().parse_production(io.BytesIO(bad_csv.encode("utf-8")))


def test_parse_brazil_prefers_state_matching_row_on_small_rounding_diff() -> None:
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
    assert row["field_id"] == "FAZENDA BELEM CE"
    assert row["field_name"] == "FAZENDA BELÉM CE"
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
    assert not (tmp_path / "raw").exists()
    assert (tmp_path / "store" / "geo" / "fields" / "brazil.parquet").exists()
    assert (tmp_path / "store" / "geo" / "facilities" / "brazil.parquet").exists()
    assert (tmp_path / "store" / "geo" / "wells" / "brazil.parquet").exists()

    fac = db.facilities(source="brazil")
    assert fac.loc[0, "facility_id"] == "FPSO ALMIRANTE BARROSO"
    assert math.isclose(float(fac.loc[0, "gas_capacity_e3m3d"]), 6291.0, rel_tol=1e-4)

    wells = db.wells(source="brazil").set_index("well_id")
    assert math.isclose(wells.loc["1-AP-1-AP"].geometry.y, -0.5, rel_tol=1e-6)
    assert wells.loc["1-NO-COORD-BA"].geometry is None

    fields = db.fields(source="brazil").set_index("field_id")
    assert fields.loc["BUZIOS", "region"] == "Rio de Janeiro"
    assert fields.loc["CANDEIAS HIST"].geometry is None

    manifest = json.loads((tmp_path / "store" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["units"]["oil_prod"] == "bbl/d"
    assert manifest["units"]["gas_prod"] == "e3m3/d"
    br_meta = manifest["sources"]["brazil"]
    assert br_meta["country"] == "Brazil"
    assert br_meta["country_code"] == "BR"
    assert br_meta["publisher"] == "ANP"
    assert "pm/producao-mar-2026.csv" in br_meta["files"]


@given(
    year=st.integers(min_value=1941, max_value=2099),
    month_idx=st.integers(min_value=1, max_value=12),
    sep=st.sampled_from(["/", "-"]),
    upper=st.booleans(),
)
def test_parse_anp_portuguese_month_periods(
    year: int, month_idx: int, sep: str, upper: bool
) -> None:
    import pandas as pd

    abbr = list(brazil._PT_MONTHS)[month_idx - 1]
    token = f"{abbr.upper() if upper else abbr}{sep}{year}"
    parsed = brazil._parse_anp_period_series(pd.Series([token]))
    ts = parsed.iloc[0]
    assert pd.notna(ts)
    assert ts.year == year
    assert ts.month == month_idx
    assert ts.day == 1


@given(
    year=st.integers(min_value=1941, max_value=2099),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28),
    fmt=st.sampled_from(["slash", "dash", "iso", "unpadded"]),
)
def test_parse_br_date_normalizes_all_anp_formats_to_iso(
    year: int, month: int, day: int, fmt: str
) -> None:
    import pandas as pd

    if fmt == "slash":
        text = f"{day:02d}/{month:02d}/{year}"
    elif fmt == "dash":
        text = f"{day:02d}-{month:02d}-{year}"
    elif fmt == "unpadded":
        text = f"{day}/{month}/{year}"
    else:
        text = f"{year}-{month:02d}-{day:02d}"
    parsed = brazil._parse_br_date(pd.Series([text]))
    assert parsed.iloc[0] == f"{year}-{month:02d}-{day:02d}"


def test_parse_brazil_portuguese_months_and_fail_loud_bad_period() -> None:
    csv_ok = (
        "[Ano],[Mês/Ano],[Campo],[Bacia],[Instalação],[Poço],[Estado],[Ambiente],"
        "[Produção de Óleo (m³)],[Produção de Gás Associado (Mm³)]\n"
        "2025,out/2025,LAGOA SURUACA,Espírito Santo,Petrobras 66 (P-66),7-LS-14-ES,"
        'Não Informado,TERRA,"158,99","31,0"\n'
        "2025,dez/2025,LAGOA SURUACA,Espírito Santo,FPSO CIDADE DE ILHA BELA,7-LS-14-ES,"
        '(NÃO CADASTRADO),TERRA,"158,99","31,0"\n'
    )
    table = brazil.Brazil().parse_production(io.BytesIO(csv_ok.encode("utf-8")))
    df = table.to_pandas().sort_values("period").reset_index(drop=True)
    assert len(df) == 2
    assert df.loc[0, "period"].isoformat() == "2025-10-01"
    assert df.loc[1, "period"].isoformat() == "2025-12-01"
    assert df.loc[0, "facility_id"] == "PETROBRAS 66"
    assert df.loc[1, "facility_id"] == "FPSO CIDADE DE ILHABELA"
    assert df["region"].isna().all()

    csv_bad = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,Produção de Óleo (m³)\n"
        "2025,bogus/2025,Bahia,Recôncavo,CAMPO A,1-A-1-BA,Terra,Não Informado,10\n"
    )
    with pytest.raises(ValueError, match="unparseable period"):
        brazil.Brazil().parse_production(io.BytesIO(csv_bad.encode("utf-8")))


def test_brazil_wfs_dates_env_backfill_status_and_dynamic_wells_url(tmp_path: Path) -> None:
    wfs_payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "COD_CAMPO": 10,
                    "NOM_CAMPO": "CANDEIAS",
                    "NOM_BACIA": "Recôncavo",
                    "OPERADOR_C": None,
                    "AMBIENTE": "A ",
                    "ETAPA": "Em Devolução\n",
                    "FLUIDO_PRI": "ÓLEO",
                    "MED_LAMINA": 0,
                    "AREA": 109,
                    "DAT_DESCOB": "12/05/1941",
                    "DAT_INICIO": "1-11-1941",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[-38.6, -12.7], [-38.5, -12.7], [-38.5, -12.6], [-38.6, -12.7]]
                    ],
                },
            }
        ],
    }
    wfs_path = tmp_path / "campos.geojson"
    wfs_path.write_text(json.dumps(wfs_payload), encoding="utf-8")
    active_path = tmp_path / "extracao_campo.csv"
    active_path.write_text(
        "CODIGO_CAMPO,CAMPO,BACIA,ESTADO,OPERADOR,AMBIENTE,SITUACAO_CAMPO,DATA_INICIO_PRODUCAO\n"
        "10,CANDEIAS,Recôncavo,Bahia,PETROBRAS,Terra,Produção,01/11/1941\n",
        encoding="utf-8-sig",
    )
    relinq_path = tmp_path / "extracao_campo_devolvido.csv"
    relinq_path.write_text(
        "CODIGO_CAMPO,CAMPO,BACIA,AMBIENTE,SITUACAO,ULTIMO_MES_PRODUCAO\n"
        "90,DENTÃO,Potiguar,MAR,Em Devolução,12/2006\n",
        encoding="utf-8-sig",
    )
    fields = (
        brazil.Brazil()
        .parse_geo(schema.GeoKind.FIELDS, [wfs_path, active_path, relinq_path])
        .set_index("field_id")
    )
    assert fields.loc["CANDEIAS", "discovery_date"] == "1941-05-12"
    assert fields.loc["CANDEIAS", "production_start_date"] == "1941-11-01"
    assert fields.loc["CANDEIAS", "environment"] == "onshore"
    assert fields.loc["CANDEIAS", "operator"] == "PETROBRAS"
    assert fields.loc["CANDEIAS", "status"] == "Em Devolução"
    assert fields.loc["DENTAO", "status"] == "Em Devolução"

    portal_html = (
        '<a href="https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/'
        "arquivos-fase-de-desenvolvimento-e-producao/informacoes-sobre-pocos/"
        'situacao-pocos-1939-2027.csv">wells</a>'
    )
    client = httpx2.Client(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, text=portal_html))
    )
    assert brazil._discover_wells_url(client).endswith("situacao-pocos-1939-2027.csv")


def test_parse_national_gas_csv_aggregates_states_and_environments() -> None:
    csv_text = (
        "ANO;MÊS;GRANDE REGIÃO;UNIDADE DA FEDERAÇÃO;PRODUTO;LOCALIZAÇÃO;PRODUÇÃO\n"
        "2004;FEV;REGIÃO SUDESTE;RIO DE JANEIRO;GÁS NATURAL;MAR;1.000.000,5\n"
        "2004;FEV;REGIÃO NORTE;AMAZONAS;GÁS NATURAL;TERRA;319.176,72\n"
        "2004;MAR;REGIÃO SUDESTE;RIO DE JANEIRO;GÁS NATURAL;MAR;1.433.681\n"
    )
    totals = brazil.parse_national_gas_csv(io.BytesIO(csv_text.encode("utf-8-sig")))
    assert list(totals) == [date(2004, 2, 1), date(2004, 3, 1)]
    assert math.isclose(totals[date(2004, 2, 1)], 1_319_177.22, rel_tol=1e-9)
    assert math.isclose(totals[date(2004, 3, 1)], 1_433_681.0, rel_tol=1e-9)

    with pytest.raises(ValueError, match="not an ANP ppgn-el"):
        brazil.parse_national_gas_csv(io.BytesIO(b"foo;bar\n1;2\n"))


def test_national_gas_gap_volumes_cover_full_known_gap() -> None:
    gap = brazil.KNOWN_GAPS[0]
    assert gap.resolved_in_country_rollup is True
    expected_months: list[date] = []
    cur = gap.start
    while cur <= gap.end:
        expected_months.append(cur)
        cur = date(cur.year + (cur.month // 12), (cur.month % 12) + 1, 1)
    assert list(brazil.NATIONAL_GAS_GAP_VOLUMES_E3M3) == expected_months
    assert all(v > 1_000_000.0 for v in brazil.NATIONAL_GAS_GAP_VOLUMES_E3M3.values())


@given(
    year=st.integers(min_value=1997, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    volume=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
)
def test_national_gas_gap_rates_round_trips_calendar_days(
    year: int, month: int, volume: float
) -> None:
    period = date(year, month, 1)
    rates = brazil.national_gas_gap_rates({period: volume})
    days = calendar.monthrange(year, month)[1]
    assert math.isclose(rates[period] * days, volume, rel_tol=1e-12, abs_tol=1e-9)


_KNOWN_RAW_FIELDS = sorted(
    {
        *brazil._FIELD_RENAME_ALIASES,
        *brazil._FIELD_SPLIT_ALIASES,
        *(fid for fid, _ in brazil._FIELD_BASIN_DISAMBIGUATION),
        "BUZIOS_ECO",
        "ATAPU_ECO",
        "SEPIA_ECO",
        "ITAPU_ECO",
        "LULA_ECO",
        "MERO_ECO",
        "SERRA_ECO",
        "MARLIM",
        "SUL DE TUPI",
    }
)
_KNOWN_BASINS = ["", "SANTOS", "CAMPOS", "POTIGUAR", "RECONCAVO", "SERGIPE", "ESPIRITO SANTO"]


@example(raw_id="ANC_MERO", basin="SERGIPE")
@example(raw_id="MERO_ECO", basin="SERGIPE")
@example(raw_id="LULA_ECO", basin="SANTOS")
@given(
    raw_id=st.sampled_from(_KNOWN_RAW_FIELDS),
    basin=st.sampled_from(_KNOWN_BASINS),
)
def test_canonicalize_field_id_is_idempotent(raw_id: str, basin: str) -> None:
    once = brazil._canonicalize_field_id(raw_id, basin)
    twice = brazil._canonicalize_field_id(once, basin)
    assert once == twice
    assert not once.endswith("_ECO")
    assert not once.startswith("ANC_")


@example(base_name="LULA", basin="SANTOS")
@example(base_name="MERO", basin="SERGIPE")
@given(
    base_name=st.one_of(
        st.sampled_from([f for f in _KNOWN_RAW_FIELDS if not f.endswith("_ECO")]),
        st.from_regex(r"[A-Z]{2,12}", fullmatch=True).filter(
            lambda s: (
                s not in brazil._MISSING_SENTINELS
                and not s.endswith("_ECO")
                and not s.startswith("ANC_")
            )
        ),
    ),
    basin=st.sampled_from(_KNOWN_BASINS),
)
def test_canonicalize_field_id_strips_any_eco_suffix(base_name: str, basin: str) -> None:
    assert brazil._canonicalize_field_id(
        f"{base_name}_ECO", basin
    ) == brazil._canonicalize_field_id(base_name, basin)


def _fmt_br_opt(val: float | None) -> str:
    return "" if val is None else f'"{val:.2f}"'.replace(".", ",")


_OPT_VOL = st.one_of(
    st.none(),
    st.floats(min_value=0.0, max_value=50_000.0, allow_nan=False, allow_infinity=False),
)


@given(
    oil_a=_OPT_VOL,
    oil_b=_OPT_VOL,
    gas_a=_OPT_VOL,
    gas_b=_OPT_VOL,
    winj_a=_OPT_VOL,
    winj_b=_OPT_VOL,
)
def test_eco_split_merge_conserves_volume_and_preserves_nulls(
    oil_a: float | None,
    oil_b: float | None,
    gas_a: float | None,
    gas_b: float | None,
    winj_a: float | None,
    winj_b: float | None,
) -> None:
    import pandas as pd

    csv_text = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,"
        "Produção de Óleo (m³),Produção de Gás Associado (Mm³),"
        "Injeção de Água para Recuperação Secundária (m³)\n"
        f"2026,01/2026,Rio de Janeiro,Santos,BÚZIOS,7-BUZ-1-RJS,Mar,PETROBRAS 78,"
        f"{_fmt_br_opt(oil_a)},{_fmt_br_opt(gas_a)},{_fmt_br_opt(winj_a)}\n"
        f"2026,01/2026,Rio de Janeiro,Santos,BÚZIOS_ECO,7-BUZ-1-RJS,Mar,PETROBRAS 78,"
        f"{_fmt_br_opt(oil_b)},{_fmt_br_opt(gas_b)},{_fmt_br_opt(winj_b)}\n"
    )
    df = brazil.Brazil().parse_production(io.BytesIO(csv_text.encode("utf-8"))).to_pandas()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["field_id"] == "BUZIOS"
    assert row["field_name"] == "BÚZIOS"

    for col, a, b, scale in (
        ("oil_prod", oil_a, oil_b, units.BBL_PER_M3 / 31.0),
        ("gas_prod", gas_a, gas_b, 1.0 / 31.0),
        ("water_inj", winj_a, winj_b, units.BBL_PER_M3 / 31.0),
    ):
        if a is None and b is None:
            assert pd.isna(row[col])
        else:
            expected = (round(a or 0.0, 2) + round(b or 0.0, 2)) * scale
            assert math.isclose(float(row[col]), expected, rel_tol=1e-4, abs_tol=1e-3)


def test_parse_brazil_merges_eco_anc_renames_annexations_and_homonyms() -> None:
    csv_text = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,"
        "Produção de Óleo (m³),Produção de Gás Associado (Mm³),"
        "Injeção de Gás (Mm³),Injeção de Gás Carbônico (Mm³)\n"
        # Cat 1 + Cat 3: ATAPU + ATAPU_ECO + ANC_NORTE_ATAPU summed; OESTE DE ATAPU kept separate
        "2026,01/2026,Rio de Janeiro,Santos,ATAPU,7-ATP-6-RJS,Mar,PETROBRAS 70,100,10,5,2\n"
        "2026,01/2026,Rio de Janeiro,Santos,ATAPU_ECO,7-ATP-6-RJS,Mar,PETROBRAS 70,150,15,7,3\n"
        "2026,01/2026,Rio de Janeiro,Santos,ANC_NORTE_ATAPU,7-ATP-6-RJS,Mar,PETROBRAS 70,50,5,3,1\n"
        "2026,01/2026,Rio de Janeiro,Santos,OESTE DE ATAPU,7-ATP-6-RJS,Mar,PETROBRAS 70,40,4,0,0\n"
        # Cat 2 + Cat 3: LULA + ANC_LULA -> summed into TUPI; SUL DE LULA -> SUL DE TUPI
        "2019,12/2019,Rio de Janeiro,Santos,LULA,7-LL-1-RJS,Mar,PETROBRAS 66,200,20,0,0\n"
        "2019,12/2019,Rio de Janeiro,Santos,ANC_LULA,7-LL-1-RJS,Mar,PETROBRAS 66,20,2,0,0\n"
        "2019,12/2019,Rio de Janeiro,Santos,SUL DE LULA,7-LL-1-RJS,Mar,PETROBRAS 66,30,3,0,0\n"
        # Cat 3 + Cat 6: MERO (Santos) + AnC_MERO summed; MERO (Sergipe) -> MERO SE
        "2026,01/2026,Rio de Janeiro,Santos,MERO,7-MRO-1-RJS,Mar,FPSO GUANABARA,300,30,0,0\n"
        "2026,01/2026,Rio de Janeiro,Santos,AnC_MERO,7-MRO-1-RJS,Mar,FPSO GUANABARA,10,1,0,0\n"
        "1976,06/1976,,Sergipe,MERO,7-ME-3D-ALS,Mar,Não Informado,60,0,0,0\n"
        # Cat 4a: Parque das Baleias -> JUBARTE; SURURU -> BERBIGÃO; SERRA+ARATUM+MACAU -> MACAU
        "2019,04/2019,Espírito Santo,Campos,BALEIA AZUL,7-BAZ-1-ESS,Mar,PETROBRAS 58,90,9,0,0\n"
        "2024,12/2024,Rio de Janeiro,Santos,SURURU,7-SRR-1-RJS,Mar,PETROBRAS 68,80,8,0,0\n"
        "2021,03/2021,Rio Grande do Norte,Potiguar,SERRA,7-MA-31D-RNS,Mar,ECM,70,7,0,0\n"
        "2021,03/2021,Rio Grande do Norte,Potiguar,ARATUM,7-MA-31D-RNS,Mar,ECM,20,2,0,0\n"
        "2021,03/2021,Rio Grande do Norte,Potiguar,MACAU,7-MA-31D-RNS,Mar,ECM,10,1,0,0\n"
        # Cat 2 typos + Cat 6 homonyms
        "1990,01/1990,Bahia,Recôncavo,BELA VISTA_,1-BV-1-BA,Terra,Não Informado,10,0,0,0\n"
        "1990,01/1990,Bahia,Recôncavo,DIAS D'ÁVILA,1-DA-1-BA,Terra,Não Informado,10,0,0,0\n"
        "1990,01/1990,Bahia,Recôncavo,FAZENDA BELÉM,7-FZB-1-BA,Terra,Não Informado,15,0,0,0\n"
        "1990,01/1990,Rio Grande do Norte,Potiguar,BREJINHO,7-BRJ-1-RN,Terra,"
        "Não Informado,12,0,0,0\n"
        "1990,01/1990,Bahia,Recôncavo,BREJINHO,7-BRJ-1-BA,Terra,Não Informado,8,0,0,0\n"
    )
    df = brazil.Brazil().parse_production(io.BytesIO(csv_text.encode("utf-8"))).to_pandas()
    by_key = df.set_index(["entity_id", "field_id"])

    atp = by_key.loc[("7-ATP-6-RJS", "ATAPU")]
    assert math.isclose(float(atp["oil_prod"]), 300.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)
    assert math.isclose(float(atp["gas_inj"]), 15.0 / 31.0, rel_tol=1e-4)
    assert math.isclose(float(atp["co2_inj"]), 6.0 / 31.0, rel_tol=1e-4)
    assert ("7-ATP-6-RJS", "OESTE DE ATAPU") in by_key.index

    tupi = by_key.loc[("7-LL-1-RJS", "TUPI")]
    assert tupi["field_name"] == "TUPI"
    assert math.isclose(float(tupi["oil_prod"]), 220.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)
    assert ("7-LL-1-RJS", "SUL DE TUPI") in by_key.index

    mero = by_key.loc[("7-MRO-1-RJS", "MERO")]
    assert math.isclose(float(mero["oil_prod"]), 310.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)
    assert ("7-ME-3D-ALS", "MERO SE") in by_key.index

    assert by_key.loc[("7-BAZ-1-ESS", "JUBARTE"), "field_name"] == "JUBARTE"
    assert by_key.loc[("7-SRR-1-RJS", "BERBIGAO"), "field_name"] == "BERBIGÃO"
    macau = by_key.loc[("7-MA-31D-RNS", "MACAU")]
    assert math.isclose(float(macau["oil_prod"]), 100.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)

    assert ("1-BV-1-BA", "BELA VISTA") in by_key.index
    assert ("1-DA-1-BA", "DIAS DAVILA") in by_key.index
    assert by_key.loc[("7-FZB-1-BA", "FAZENDA BELEM BA"), "field_name"] == "FAZENDA BELÉM BA"
    assert ("7-BRJ-1-RN", "BREJINHO RN") in by_key.index
    assert ("7-BRJ-1-BA", "BREJINHO BA") in by_key.index


def test_parse_brazil_deduplicates_pre2005_norte_de_pescada_into_dentao() -> None:
    csv_text = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,"
        "Produção de Óleo (m³),Produção de Gás Associado (Mm³)\n"
        "2004,01/2004,Rio Grande do Norte,Potiguar,DENTÃO,3-RNS-33-RNS,Mar,Não Informado,100,50\n"
        "2004,01/2004,Rio Grande do Norte,Potiguar,NORTE DE PESCADA,3-RNS-33-RNS,Mar,"
        "Não Informado,100,0\n"
    )
    with pytest.warns(UserWarning, match="2003-07..2005-06"):
        df = brazil.Brazil().parse_production(io.BytesIO(csv_text.encode("utf-8"))).to_pandas()
    assert len(df) == 1
    row = df.iloc[0]
    assert row["field_id"] == "DENTAO"
    assert row["field_name"] == "DENTÃO"
    # Deduplicated (100 m³), NOT double-counted (200 m³).
    assert math.isclose(float(row["oil_prod"]), 100.0 * units.BBL_PER_M3 / 31.0, rel_tol=1e-4)
    assert math.isclose(float(row["gas_prod"]), 50.0 / 31.0, rel_tol=1e-4)


def test_brazil_geo_reconciles_eco_annexations_wells_and_facilities(tmp_path: Path) -> None:
    wfs_payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "COD_CAMPO": 4827,
                    "NOM_CAMPO": "BÚZIOS_ECO",
                    "NOM_BACIA": "Santos",
                    "OPERADOR_C": "PETROBRAS",
                    "AMBIENTE": "M",
                    "ETAPA": "Produção",
                    "FLUIDO_PRI": "ÓLEO",
                    "MED_LAMINA": 1900,
                    "AREA": 852.0,
                    "DAT_DESCOB": "2010-05-01",
                    "DAT_INICIO": "2021-09-01",
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
            },
            {
                "type": "Feature",
                "properties": {
                    "COD_CAMPO": 4598,
                    "NOM_CAMPO": "BÚZIOS",
                    "NOM_BACIA": "Santos",
                    "OPERADOR_C": "PETROBRAS",
                    "AMBIENTE": "M",
                    "ETAPA": "Produção",
                    "FLUIDO_PRI": "ÓLEO",
                    "MED_LAMINA": 1900,
                    "AREA": 852.0,
                    "DAT_DESCOB": "2010-05-01",
                    "DAT_INICIO": "2015-03-10",
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
            },
            {
                "type": "Feature",
                "properties": {
                    "COD_CAMPO": 152,
                    "NOM_CAMPO": "JUBARTE",
                    "NOM_BACIA": "Campos",
                    "OPERADOR_C": "PETROBRAS",
                    "AMBIENTE": "M",
                    "ETAPA": "Produção",
                    "FLUIDO_PRI": "ÓLEO",
                    "MED_LAMINA": 1300,
                    "AREA": 398.0,
                    "DAT_DESCOB": "2001-01-01",
                    "DAT_INICIO": "2002-12-12",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-40.1, -21.3],
                            [-40.0, -21.3],
                            [-40.0, -21.2],
                            [-40.1, -21.2],
                            [-40.1, -21.3],
                        ]
                    ],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "COD_CAMPO": 65,
                    "NOM_CAMPO": "CACHALOTE",
                    "NOM_BACIA": "Campos",
                    "OPERADOR_C": "PETROBRAS",
                    "AMBIENTE": "M",
                    "ETAPA": "Em Devolução",
                    "FLUIDO_PRI": "ÓLEO",
                    "MED_LAMINA": 1400,
                    "AREA": 61.0,
                    "DAT_DESCOB": "2002-06-01",
                    "DAT_INICIO": "2008-12-16",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [-40.0, -21.3],
                            [-39.9, -21.3],
                            [-39.9, -21.2],
                            [-40.0, -21.2],
                            [-40.0, -21.3],
                        ]
                    ],
                },
            },
        ],
    }
    wfs_path = tmp_path / "campos.geojson"
    wfs_path.write_text(json.dumps(wfs_payload), encoding="utf-8")
    active_csv = tmp_path / "extracao_campo.csv"
    active_csv.write_text(
        "CODIGO_CAMPO,CAMPO,BACIA,ESTADO,OPERADOR,AMBIENTE,SITUACAO_CAMPO,DATA_INICIO_PRODUCAO\n"
        "4827,BÚZIOS_ECO,Santos,Rio de Janeiro,PETROBRAS,Mar,Produção,01/09/2021\n"
        "4598,BÚZIOS,Santos,Rio de Janeiro,PETROBRAS,Mar,Produção,10/03/2015\n",
        encoding="utf-8-sig",
    )
    fields = (
        brazil.Brazil()
        .parse_geo(schema.GeoKind.FIELDS, [wfs_path, active_csv])
        .set_index("field_id")
    )
    assert set(fields.index) == {"BUZIOS", "JUBARTE"}
    assert fields.loc["BUZIOS", "field_code"] == "4598"
    assert fields.loc["BUZIOS", "field_name"] == "BÚZIOS"
    assert fields.loc["BUZIOS", "production_start_date"] == "2015-03-10"
    assert math.isclose(float(fields.loc["BUZIOS", "area_km2"]), 852.0, rel_tol=1e-4)

    assert fields.loc["JUBARTE", "field_code"] == "152"
    assert fields.loc["JUBARTE", "status"] == "Produção"
    assert fields.loc["JUBARTE", "production_start_date"] == "2002-12-12"
    assert math.isclose(float(fields.loc["JUBARTE", "area_km2"]), 459.0, rel_tol=1e-4)
    assert math.isclose(fields.loc["JUBARTE"].geometry.area, 0.02, rel_tol=1e-3)

    wells_csv = tmp_path / "wells.csv"
    wells_csv.write_text(
        "NOME_POCO_ANP,CAMPO,BACIA,ESTADO,AMBIENTE,LATITUDE_DA_BASE,LONGITUDE_DA_BASE\n"
        "7-BUZ-99-RJS,BÚZIOS_ECO,Santos,Rio de Janeiro,Mar,-24:36:00,-42:30:00\n"
        "7-MRO-99-RJS,AnC_MERO,Santos,Rio de Janeiro,Mar,-24:40:00,-42:10:00\n"
        "7-LL-99-RJS,LULA,Santos,Rio de Janeiro,Mar,-25:30:00,-42:50:00\n",
        encoding="utf-8-sig",
    )
    wells = brazil.Brazil().parse_geo(schema.GeoKind.WELLS, [wells_csv]).set_index("well_id")
    assert wells.loc["7-BUZ-99-RJS", "field_id"] == "BUZIOS"
    assert wells.loc["7-BUZ-99-RJS", "field_name"] == "BÚZIOS"
    assert wells.loc["7-MRO-99-RJS", "field_id"] == "MERO"
    assert wells.loc["7-LL-99-RJS", "field_id"] == "TUPI"

    lpo_csv = tmp_path / "lpo.csv"
    lpo_csv.write_text(
        "NOME DA INSTALAÇÃO,SIGLA DA INSTALAÇÃO,CAMPOS,BACIA,OPERADOR,LATITUDE,LONGITUDE\n"
        "PETROBRAS 78,P-78,BÚZIOS / BÚZIOS_ECO / TAMBUATÁ,Santos,PETROBRAS,-24:36:00,-42:30:00\n"
        "PETROBRAS 66,P-66,AnC_TUPI / SUL DE TUPI / TUPI,Santos,PETROBRAS,-25:30:00,-42:50:00\n",
        encoding="utf-8-sig",
    )
    facs = brazil.Brazil().parse_geo(schema.GeoKind.FACILITIES, [lpo_csv]).set_index("facility_id")
    assert facs.loc["PETROBRAS 78", "fields"] == "BÚZIOS / TAMBUATÁ"
    assert facs.loc["PETROBRAS 66", "fields"] == "SUL DE TUPI / TUPI"
