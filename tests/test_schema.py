import pandas as pd
import pytest

from ogprod import schema


def _valid_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "source": ["anp"],
            "entity_type": ["well"],
            "entity_id": ["7-TPI-1-RJS"],
            "entity_name": ["7-TPI-1-RJS"],
            "field": ["TUPI"],
            "basin": ["Santos"],
            "region": ["Rio de Janeiro"],
            "operator": ["Petrobras"],
            "period": [pd.Timestamp("2024-01-01")],
            "product": ["oil"],
            "volume": [12345.6],
        }
    )


def test_empty_frame_is_valid():
    frame = schema.empty_frame()
    assert list(frame.columns) == list(schema.CANONICAL_COLUMNS)
    schema.validate(frame)


def test_valid_frame_passes():
    frame = _valid_frame()
    assert schema.validate(frame) is frame


def test_missing_column():
    with pytest.raises(schema.SchemaError, match="missing canonical columns"):
        schema.validate(_valid_frame().drop(columns=["volume"]))


def test_bad_product():
    frame = _valid_frame().assign(product="crude")
    with pytest.raises(schema.SchemaError, match="invalid product"):
        schema.validate(frame)


def test_bad_entity_type():
    frame = _valid_frame().assign(entity_type="lease")
    with pytest.raises(schema.SchemaError, match="invalid entity_type"):
        schema.validate(frame)


def test_period_must_be_month_start():
    frame = _valid_frame().assign(period=pd.Timestamp("2024-01-15"))
    with pytest.raises(schema.SchemaError, match="first day of the month"):
        schema.validate(frame)


def test_canonical_id():
    assert schema.canonical_id("anp", "7-TPI-1-RJS") == "anp:7-TPI-1-RJS"
