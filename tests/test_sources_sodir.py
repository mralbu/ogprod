import pandas as pd
import pytest

import ogprod
from ogprod import schema


def test_parse_field_production_monthly(data_dir):
    frame = ogprod.load("sodir", data_dir / "sodir_field_production_monthly_sample.csv")
    schema.validate(frame)

    assert set(frame["entity_type"]) == {"field"}
    assert set(frame["period"]) == {pd.Timestamp("2024-05-01")}
    # 2 fields x 5 products (oil equivalent column is not a product)
    assert len(frame) == 10

    sverdrup = frame[frame["entity_name"] == "JOHAN SVERDRUP"]
    assert set(sverdrup["entity_id"]) == {"26376286"}  # stable NPDID, not the name

    oil = sverdrup[sverdrup["product"] == "oil"].iloc[0]
    assert oil["volume"] == pytest.approx(2.1e6)  # million Sm3 -> m3

    troll_gas = frame[(frame["entity_name"] == "TROLL") & (frame["product"] == "gas")].iloc[0]
    assert troll_gas["volume"] == pytest.approx(3.6e9)  # billion Sm3 -> m3


def test_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="Sodir"):
        ogprod.load("sodir", bogus)
