import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

import ogprod
from ogprod import schema
from ogprod.sources import base, get


def test_builtin_sources_registered():
    assert ogprod.available() == ["anp", "argentina", "sodir"]


def test_get_returns_source_instance():
    source = get("sodir")
    assert source.name == "sodir"
    assert source.entity_type == schema.EntityType.FIELD


def test_unknown_source():
    with pytest.raises(KeyError, match="unknown source"):
        get("opec")


def test_fetch_default_not_implemented_mentions_docs_url():
    class NoFetch(base.Source):
        name = "nofetch"
        entity_type = schema.EntityType.WELL
        docs_url = "https://example.org/data"

        def parse(self, raw):
            raise NotImplementedError

    with pytest.raises(NotImplementedError, match="https://example.org/data"):
        NoFetch().fetch("/tmp")


def test_source_without_entities_raises_pointed_error():
    class NoEntities(base.Source):
        name = "noentities"
        entity_type = schema.EntityType.WELL
        docs_url = "https://example.org/wells"

        def parse(self, raw):
            raise NotImplementedError

    connector = NoEntities()
    with pytest.raises(NotImplementedError, match="noentities"):
        connector.parse_entities("whatever.csv")
    with pytest.raises(NotImplementedError, match="https://example.org/wells"):
        connector.fetch_entities("dest")


def _frame_kwargs(**overrides):
    kwargs = dict(
        source="testsrc",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["W-1", "W-2"]),
        entity_name=pd.Series(["W-1", "W-2"]),
        period=pd.Series([pd.Timestamp("2024-01-01")] * 2),
        volumes_m3={schema.Product.OIL: pd.Series([1.0, 2.0])},
    )
    kwargs.update(overrides)
    return kwargs


def test_build_frame_drops_rows_with_null_period():
    frame = base.build_frame(
        **_frame_kwargs(period=pd.Series([pd.Timestamp("2024-01-01"), pd.NaT]))
    )
    assert list(frame["entity_id"]) == ["W-1"]


def test_build_frame_drops_rows_with_null_entity_id():
    frame = base.build_frame(**_frame_kwargs(entity_id=pd.Series(["W-1", None])))
    assert list(frame["entity_id"]) == ["W-1"]


def test_build_frame_normalizes_float_promoted_ids():
    # A null elsewhere in a numeric id column promotes it to float64; the id
    # must come out "50136", not "50136.0", or joins silently break.
    frame = base.build_frame(**_frame_kwargs(entity_id=pd.Series([50136.0, None])))
    assert list(frame["entity_id"]) == ["50136"]


def test_build_frame_preserves_string_ids_with_leading_zeros():
    frame = base.build_frame(**_frame_kwargs(entity_id=pd.Series(["007", "008"])))
    assert list(frame["entity_id"]) == ["007", "008"]


def test_build_entities_drops_null_id_rows_and_normalizes_float_ids():
    entities = base.build_entities(
        source="testsrc",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series([50136.0, None]),
        entity_name=pd.Series(["A", "B"]),
        geometry=gpd.GeoSeries([Point(-68.9, -38.6), Point(-68.8, -38.5)], crs="EPSG:4326"),
    )
    assert list(entities["entity_id"]) == ["50136"]


def test_build_entities_assembles_and_reprojects():
    entities = base.build_entities(
        source="anp",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["7-TPI-1-RJS"]),
        entity_name=pd.Series(["7-TPI-1-RJS"]),
        geometry=gpd.GeoSeries([Point(500000, 7525000)], crs="EPSG:32723"),
        crs="EPSG:32723",
    )
    schema.validate_entities(entities)
    assert list(entities.columns) == list(schema.ENTITY_COLUMNS)
    assert entities.crs.to_epsg() == 4326
    point = entities.geometry.iloc[0]
    assert point.x == pytest.approx(-45.0, abs=0.1)  # UTM 23S -> lon/lat
    assert point.y == pytest.approx(-22.38, abs=0.1)
    assert entities["field"].isna().all()  # optional metadata defaults to NA


def test_top_level_surface_is_small():
    assert sorted(ogprod.__all__) == ["Product", "SchemaError", "Store", "available", "convert"]


def test_fetch_rejects_unknown_options(tmp_path):
    # A typo must not silently fall back to "download everything".
    with pytest.raises(TypeError):
        get("anp").fetch(tmp_path, yaer=2023)  # ty: ignore[unknown-argument]
