import pandas as pd
import pytest

from ogprod import schema
from ogprod.sources import get


def _row(frame: pd.DataFrame, entity: str, product: str) -> pd.Series:
    match = frame[(frame["entity_id"] == entity) & (frame["product"] == product)]
    assert len(match) == 1
    return match.iloc[0]


def test_parse_well_file(data_dir):
    frame = get("anp").parse(data_dir / "anp_pocos_sample.csv")
    schema.validate(frame)

    # 2 wells x 3 products
    assert len(frame) == 6
    assert set(frame["source"]) == {"anp"}
    assert set(frame["entity_type"]) == {"well"}

    oil = _row(frame, "7-TPI-1-RJS", "oil")
    assert oil["volume"] == pytest.approx(12345.6)  # m3, decimal comma parsed
    assert oil["field"] == "TUPI"
    assert oil["basin"] == "Santos"
    assert oil["region"] == "Rio de Janeiro"
    assert oil["operator"] == "Petrobras"
    assert oil["period"] == pd.Timestamp("2024-01-01")

    gas = _row(frame, "7-CAM-99-RN", "gas")
    assert gas["volume"] == pytest.approx(12.3 * 1000)  # Mm3 = thousand m3

    water = _row(frame, "7-CAM-99-RN", "water")
    assert water["volume"] == pytest.approx(980.2)


def test_parse_daily_rate_and_periodo_column(data_dir):
    frame = get("anp").parse(data_dir / "anp_pocos_rate_units.csv")
    schema.validate(frame)

    row = frame.iloc[0]
    assert row["period"] == pd.Timestamp("2024-02-01")
    # 1000 bbl/day over the 29 days of February 2024, converted to m3
    assert row["volume"] == pytest.approx(1000 * 29 * 0.158987294928)


def test_parse_slash_period_format(tmp_path):
    # Real bulk files write the period as "2023/01", not "2023-01".
    file = tmp_path / "poco.csv"
    file.write_text("Poço;Período;Petróleo (m³)\n7-X-1-BA;2023/01;10,0\n", encoding="utf-8")
    frame = get("anp").parse(file)
    assert frame.iloc[0]["period"] == pd.Timestamp("2023-01-01")


def test_oleo_preferred_over_petroleo_total(tmp_path):
    # In files carrying both, "Petróleo" is the óleo+condensado total; counting
    # it alongside "Óleo" would double-count oil.
    file = tmp_path / "poco.csv"
    file.write_text(
        "Poço;Período;Óleo (m³);Condensado (m³);Petróleo (m³)\n7-X-1-BA;2023/01;100,0;20,0;120,0\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)
    oil = frame[frame["product"] == "oil"].iloc[0]
    assert oil["volume"] == pytest.approx(100.0)
    condensate = frame[frame["product"] == "condensate"].iloc[0]
    assert condensate["volume"] == pytest.approx(20.0)


def test_parse_bulk_file_multirow_header(tmp_path):
    # Bulk files (2021+) use a three-row header: main labels, sub-labels
    # ("ANP"/"Operador" under "Nome Poço", "Associado"/"Não Associado"/"Gás
    # Total" under "Gás Natural"), and a third sub-sub row.
    file = tmp_path / "producao_mar.csv"
    file.write_text(
        "Estado;Bacia;Nome Poço;;Campo;Operador;Período;Óleo (bbl/dia);"
        "Condensado (bbl/dia);Petróleo (bbl/dia);Gás Natural (Mm³/dia);;;Água (bbl/dia)\n"
        ";;ANP;Operador;;;;;;;Associado;Não Associado;Gás Total;\n"
        ";;;;;;;;;;;;;\n"
        "Bahia;Recôncavo;7-FMO-1-BA;FMO-0001;FAZENDA MODELO;Petrobras;2023/01;"
        "1.100,0;20,0;1.120,0;1,0;2,0;3,0;50,0\n",  # 1.100,0 = thousands separator
        encoding="utf-8",
    )
    frame = get("anp").parse(file)

    assert set(frame["entity_id"]) == {"7-FMO-1-BA"}  # ANP name, not operator's
    assert set(frame["period"]) == {pd.Timestamp("2023-01-01")}

    oil = frame[frame["product"] == "oil"].iloc[0]
    assert oil["volume"] == pytest.approx(1100.0 * 31 * 0.158987294928)

    # gas comes from "Gás Total" alone, not total + components triple-counted
    gas = frame[frame["product"] == "gas"].iloc[0]
    assert gas["volume"] == pytest.approx(3.0 * 1000 * 31)


def test_parse_yearly_archive_with_report_preamble(tmp_path):
    # Files in the 2005-2020 yearly archives open with a SIGEP report banner
    # before the (also multi-row) header.
    file = tmp_path / "2005_producao_mar.csv"
    file.write_text(
        "ANP - Agência Nacional do Petroleo, Gás Natural e Biocombustíveis;;;;\n"
        "SIGEP Produção - Versão 9.19, build 37 (Oracle 11g);;;;\n"
        "BMP - Produção de Petróleo, Gás Natural e Água (Poço) por Campo;;;;\n"
        ";;;;\n"
        "Estado;Bacia;Nome Poço;;Período;Óleo (bbl/dia)\n"
        ";;ANP;Operador;;\n"
        "Sergipe;Sergipe;7-GA-7D-SES;7GA 0007D SES;2005/01;76,5937\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)

    assert set(frame["entity_id"]) == {"7-GA-7D-SES"}
    assert frame.iloc[0]["period"] == pd.Timestamp("2005-01-01")
    assert frame.iloc[0]["volume"] == pytest.approx(76.5937 * 31 * 0.158987294928)


def test_parse_cp1252_encoded_file(tmp_path):
    # Late-2023 bulk files are cp1252-encoded, with the sub-labels inlined
    # into a single header row.
    file = tmp_path / "2023_12_producao_Mar.csv"
    file.write_text(
        "Estado;Bacia;Nome Poço ANP;Nome Poço Operador;Campo;Operador;Período;Óleo (bbl/dia)\n"
        "Rio de Janeiro;Campos;7-MRL-10-RJS;MRL-10;MARLIM;Petrobras;2023/12;500,0\n",
        encoding="cp1252",
    )
    frame = get("anp").parse(file)
    assert set(frame["entity_id"]) == {"7-MRL-10-RJS"}
    assert frame.iloc[0]["period"] == pd.Timestamp("2023-12-01")


def test_parse_data_rows_shifted_left_of_header(tmp_path):
    # In 2010-2011 "terra" files the header carries an Estado column that the
    # data rows omit, so every data row sits one column left of its header.
    file = tmp_path / "2010_02_producao_terra.csv"
    file.write_text(
        "Estado;Bacia;Nome Poço;;Campo;Operador;Período;Óleo (bbl/dia);Água (bbl/dia)\n"
        ";;ANP;Operador;;;;;\n"
        "Recôncavo;7-C-10-BA;7C 0010 BA;CANDEIAS;Petrobras;2010/02;3,0;9,0\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)

    assert set(frame["entity_id"]) == {"7-C-10-BA"}
    assert set(frame["field"]) == {"CANDEIAS"}
    assert frame.iloc[0]["period"] == pd.Timestamp("2010-02-01")
    oil = frame[frame["product"] == "oil"].iloc[0]
    assert oil["volume"] == pytest.approx(3.0 * 28 * 0.158987294928)


def test_parse_drops_repeated_header_blocks(tmp_path):
    # Some files concatenate several report sections, repeating the banner and
    # header mid-file; those rows carry no period and must be dropped.
    file = tmp_path / "2020_06_producao_mar.csv"
    file.write_text(
        "Poço;Período;Petróleo (m³)\n"
        "7-A-1-BA;2020/06;10,0\n"
        "ANP - Agência Nacional do Petroleo;;\n"
        "Poço;Período;Petróleo (m³)\n"
        "7-B-2-BA;2020/06;20,0\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)
    assert set(frame["entity_id"]) == {"7-A-1-BA", "7-B-2-BA"}
    assert frame["volume"].sum() == pytest.approx(30.0)


def test_parse_drops_verbatim_repeated_rows(tmp_path):
    # 2015_producao_presal.csv repeats some rows verbatim; they are one row.
    file = tmp_path / "2015_producao_presal.csv"
    file.write_text(
        "Poço;Campo;Período;Petróleo (m³)\n"
        "3-RJS-159-RJS;PAMPO;2015/05;58,1\n"
        "3-RJS-159-RJS;PAMPO;2015/05;58,1\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)
    assert len(frame) == 1
    assert frame["volume"].iloc[0] == pytest.approx(58.1)


def test_merges_repeated_well_month_split_across_rows(tmp_path):
    # 2005_producao_terra.csv repeats well-months: both copies carry the oil,
    # only one carries the gas (the other carries the production hours).
    # The well produced the oil once, with that gas.
    file = tmp_path / "2005_producao_terra.csv"
    file.write_text(
        "Poço;Campo;Período;Óleo (bbl/dia);Gás Natural (Mm³/dia);Tempo de Produção (hs por mês)\n"
        "7-FBE-8-BA;FAZENDA BOA ESPERANÇA;2005/01;119,7032;3,1945;0\n"
        "7-FBE-8-BA;FAZENDA BOA ESPERANÇA;2005/01;119,7032;0,0000;31\n",
        encoding="utf-8",
    )
    with pytest.warns(UserWarning, match="incomplete"):  # 2005-01 is inside the gas gap
        frame = get("anp").parse(file)

    assert _row(frame, "7-FBE-8-BA", "oil")["volume"] == pytest.approx(
        119.7032 * 31 * 0.158987294928
    )
    assert _row(frame, "7-FBE-8-BA", "gas")["volume"] == pytest.approx(3.1945 * 31 * 1000)


def test_merges_crude_stream_rows_with_identical_volumes(tmp_path):
    # 2019_05_producao_mar.csv lists a well once per crude stream; the
    # volumes repeat, only the crude-quality columns differ.
    file = tmp_path / "2019_05_producao_mar.csv"
    file.write_text(
        "Poço;Campo;Período;Petróleo (bbl/dia);Grau API\n"
        "3-RNS-120-RN;ARABAIANA;2019/05;63,6567;38,0\n"
        "3-RNS-120-RN;ARABAIANA;2019/05;63,6567;41,5\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)
    assert len(frame) == 1
    assert frame["volume"].iloc[0] == pytest.approx(63.6567 * 31 * 0.158987294928)


def test_merge_tolerates_rounding_differences(tmp_path):
    file = tmp_path / "poco.csv"
    file.write_text(
        "Poço;Campo;Período;Petróleo (m³)\n7-X-1-BA;F;2023/01;100,0\n7-X-1-BA;F;2023/01;100,2\n",
        encoding="utf-8",
    )
    frame = get("anp").parse(file)
    assert frame["volume"].iloc[0] == pytest.approx(100.0)


def test_warns_on_known_gas_gap(tmp_path):
    file = tmp_path / "poco.csv"
    file.write_text(
        "Poço;Campo;Período;Petróleo (m³);Gás Natural (Mm³)\n7-X-1-BA;F;2004/03;10,0;1,0\n",
        encoding="utf-8",
    )
    with pytest.warns(UserWarning, match="gas is incomplete for 2003-07..2005-06"):
        get("anp").parse(file)


@pytest.mark.parametrize(
    "header,row",
    [
        ("Poço;Campo;Período;Petróleo (m³);Gás Natural (Mm³)", "7-X-1-BA;F;2007/03;10,0;1,0"),
        ("Poço;Campo;Período;Petróleo (m³)", "7-X-1-BA;F;2004/03;10,0"),
    ],
    ids=["outside-gap", "no-gas-reported"],
)
def test_no_gap_warning_outside_gap_or_product(tmp_path, recwarn, header, row):
    file = tmp_path / "poco.csv"
    file.write_text(f"{header}\n{row}\n", encoding="utf-8")
    get("anp").parse(file)
    assert not [w for w in recwarn if "incomplete" in str(w.message)]


def test_rejects_conflicting_rows_for_one_key(tmp_path):
    # Same well, field and month with different volumes: refuse to guess.
    file = tmp_path / "poco.csv"
    file.write_text(
        "Poço;Campo;Período;Petróleo (m³)\n7-X-1-BA;F;2023/01;10,0\n7-X-1-BA;F;2023/01;12,0\n",
        encoding="utf-8",
    )
    with pytest.raises(schema.SchemaError, match="conflicting"):
        get("anp").parse(file)


def test_rejects_file_without_well_column(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a;b\n1;2\n")
    with pytest.raises(ValueError, match="no well name column"):
        get("anp").parse(bogus)


def test_parse_entities_well_coordinates(data_dir):
    entities = get("anp").parse_entities(data_dir / "anp_tabela_pocos_sample.csv")
    schema.validate_entities(entities)
    assert len(entities) == 2
    assert set(entities["entity_type"]) == {"well"}

    assert entities.geometry.isna().sum() == 1  # 3-BRSA-496-RJS has no coordinates

    tpi = entities[entities["entity_id"] == "7-TPI-1-RJS"].iloc[0]
    assert tpi.geometry.x == pytest.approx(-40.051942)  # lon from the WKT "GEOM" column
    assert tpi.geometry.y == pytest.approx(-22.384617)
    assert tpi["field"] == "TARTARUGA"
    assert tpi["basin"] == "Campos"
    assert tpi["status"] == "Produzindo"


def test_parse_entities_decimal_degree_columns(tmp_path):
    # ANP also publishes yearly "poços públicos" cadastre extracts in the
    # semicolon/comma-decimal style with explicit lat/lon columns instead of
    # a WKT geometry column; parse_entities must handle both shapes.
    file = tmp_path / "pocos_publicos_2023.csv"
    file.write_text(
        "POCO;OPERADOR;ESTADO;BACIA;CAMPO;SITUACAO;LATITUDE_BASE_DD;LONGITUDE_BASE_DD\n"
        "7-TPI-1-RJS;PETROBRAS;Rio de Janeiro;Campos;TARTARUGA;Produzindo;-22,384617;-40,051942\n",
        encoding="utf-8",
    )
    entities = get("anp").parse_entities(file)
    schema.validate_entities(entities)
    row = entities.iloc[0]
    assert row.geometry.x == pytest.approx(-40.051942)
    assert row.geometry.y == pytest.approx(-22.384617)


def test_parse_entities_cp1252_encoded_cadastre(tmp_path):
    # Some ANP vintages are cp1252-encoded (the production reader already
    # falls back); the cadastre reader must fall back the same way instead
    # of raising UnicodeDecodeError.
    file = tmp_path / "pocos_publicos_2023.csv"
    file.write_bytes(
        (
            "POCO;OPERADOR;ESTADO;BACIA;CAMPO;SITUACAO;LATITUDE_BASE_DD;LONGITUDE_BASE_DD\n"
            "7-TPI-1-RJS;PETROBRAS;São Paulo;Campos;TARTARUGA;Produzindo;-22,384617;-40,051942\n"
        ).encode("cp1252")
    )
    entities = get("anp").parse_entities(file)
    assert entities["region"].iloc[0] == "São Paulo"


def test_parse_entities_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a;b\n1;2\n")
    with pytest.raises(ValueError, match="ANP"):
        get("anp").parse_entities(bogus)
