from typing import cast

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

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


def test_period_rejects_null():
    frame = _valid_frame().assign(period=pd.Series([pd.NaT]))
    with pytest.raises(schema.SchemaError, match="period"):
        schema.validate(frame)


def test_source_rejects_null():
    frame = _valid_frame().assign(source=pd.Series([None], dtype="object"))
    with pytest.raises(schema.SchemaError, match="source"):
        schema.validate(frame)


def test_canonical_id():
    assert schema.canonical_id("anp", "7-TPI-1-RJS") == "anp:7-TPI-1-RJS"


def _entities(**overrides) -> gpd.GeoDataFrame:
    """A minimal valid entities frame (one well, one field)."""
    base = {
        "source": ["anp", "sodir"],
        "entity_type": ["well", "field"],
        "entity_id": ["7-TPI-1-RJS", "43765"],
        "entity_name": ["7-TPI-1-RJS", "EKOFISK"],
        "field": ["TARTARUGA", "EKOFISK"],
        "basin": ["Campos", None],
        "region": ["Rio de Janeiro", "North sea"],
        "operator": ["PETROBRAS", "ConocoPhillips"],
        "status": ["Producing", "Producing"],
        "geometry": [
            Point(-40.05, -22.38),
            Polygon([(3.2, 56.5), (3.3, 56.5), (3.3, 56.6), (3.2, 56.6)]),
        ],
    }
    base.update(overrides)
    return gpd.GeoDataFrame(base, geometry="geometry", crs=overrides.pop("crs", "EPSG:4326"))


def test_validate_entities_accepts_valid_frame():
    entities = _entities()
    assert schema.validate_entities(entities) is entities


def test_validate_entities_rejects_missing_columns():
    entities = cast(gpd.GeoDataFrame, _entities().drop(columns=["status"]))
    with pytest.raises(schema.SchemaError, match="status"):
        schema.validate_entities(entities)


def test_validate_entities_rejects_wrong_crs():
    entities = _entities().to_crs(32631)
    with pytest.raises(schema.SchemaError, match="4326"):
        schema.validate_entities(entities)


def test_validate_entities_rejects_well_with_polygon():
    entities = _entities(entity_type=["well", "well"])
    with pytest.raises(schema.SchemaError, match="well"):
        schema.validate_entities(entities)


def test_validate_entities_rejects_bad_entity_type():
    entities = _entities(entity_type=["well", "platform"])
    with pytest.raises(schema.SchemaError, match="platform"):
        schema.validate_entities(entities)


def test_validate_entities_allows_missing_geometry():
    entities = _entities(geometry=[None, None])
    schema.validate_entities(entities)


def test_validate_entities_rejects_duplicate_source_entity_id():
    entities = _entities(
        source=["anp", "anp"],
        entity_type=["well", "well"],
        entity_id=["7-TPI-1-RJS", "7-TPI-1-RJS"],
        entity_name=["7-TPI-1-RJS", "7-TPI-1-RJS"],
        field=["TARTARUGA", "TARTARUGA"],
        basin=["Campos", "Campos"],
        region=["Rio de Janeiro", "Rio de Janeiro"],
        operator=["PETROBRAS", "PETROBRAS"],
        status=["Producing", "Producing"],
        geometry=[Point(-40.05, -22.38), Point(-40.06, -22.39)],
    )
    with pytest.raises(schema.SchemaError, match="duplicate"):
        schema.validate_entities(entities)


def test_empty_entities_is_valid():
    empty = schema.empty_entities()
    assert list(empty.columns) == list(schema.ENTITY_COLUMNS)
    assert len(empty) == 0
    schema.validate_entities(empty)
