import pandas as pd
import pytest

import ogprod
from ogprod import schema


def _row(frame: pd.DataFrame, entity: str, product: str) -> pd.Series:
    match = frame[(frame["entity_id"] == entity) & (frame["product"] == product)]
    assert len(match) == 1
    return match.iloc[0]


def test_parse_well_file(data_dir):
    frame = ogprod.load("anp", data_dir / "anp_pocos_sample.csv")
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
    frame = ogprod.load("anp", data_dir / "anp_pocos_rate_units.csv")
    schema.validate(frame)

    row = frame.iloc[0]
    assert row["period"] == pd.Timestamp("2024-02-01")
    # 1000 bbl/day over the 29 days of February 2024, converted to m3
    assert row["volume"] == pytest.approx(1000 * 29 * 0.158987294928)


def test_rejects_file_without_well_column(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a;b\n1;2\n")
    with pytest.raises(ValueError, match="no well name column"):
        ogprod.load("anp", bogus)
