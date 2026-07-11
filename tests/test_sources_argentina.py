import pandas as pd
import pytest

import ogprod
from ogprod import schema


def test_parse_capitulo_iv(data_dir):
    frame = ogprod.load("argentina", data_dir / "argentina_capitulo_iv_sample.csv")
    schema.validate(frame)

    # 2 wells x 3 products
    assert len(frame) == 6
    assert set(frame["entity_type"]) == {"well"}
    assert set(frame["period"]) == {pd.Timestamp("2024-03-01")}

    shale = frame[frame["entity_id"] == "130589"]
    assert set(shale["entity_name"]) == {"YPF.Nq.LACh-15(h)"}
    assert set(shale["field"]) == {"LOMA CAMPANA"}
    assert set(shale["basin"]) == {"NEUQUINA"}
    assert set(shale["region"]) == {"Neuquén"}
    assert set(shale["operator"]) == {"YPF S.A."}

    oil = shale[shale["product"] == "oil"].iloc[0]
    assert oil["volume"] == pytest.approx(2500.5)  # already m3

    gas = shale[shale["product"] == "gas"].iloc[0]
    assert gas["volume"] == pytest.approx(180.2 * 1000)  # thousands of m3 -> m3


def test_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="Capítulo IV"):
        ogprod.load("argentina", bogus)


def test_parse_entities_well_coordinates(data_dir):
    entities = ogprod.load_entities("argentina", data_dir / "argentina_listado_pozos_sample.csv")
    schema.validate_entities(entities)
    assert len(entities) == 2
    assert set(entities["entity_type"]) == {"well"}
    assert entities.geometry.isna().sum() == 1

    well = entities[entities["entity_id"] == "50136"].iloc[0]  # idpozo, as in production
    assert well.geometry.x == pytest.approx(-68.918)
    assert well.geometry.y == pytest.approx(-38.665)
    assert well["entity_name"] == "YPF.NQ.LCa-1(h)"
    assert well["field"] == "LOMA CAMPANA"
    assert well["operator"] == "YPF"  # the listing reports only the short company code
    assert well["status"] == "EXPLOTACION"


def test_parse_entities_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="pozos"):
        ogprod.load_entities("argentina", bogus)
