import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

import ogprod
from ogprod import schema
from ogprod.sources import base


def test_builtin_sources_registered():
    assert ogprod.available() == ["anp", "argentina", "sodir"]


def test_get_returns_source_instance():
    source = ogprod.get("sodir")
    assert source.name == "sodir"
    assert source.entity_type == ogprod.EntityType.FIELD


def test_unknown_source():
    with pytest.raises(KeyError, match="unknown source"):
        ogprod.get("opec")


def test_fetch_default_not_implemented_mentions_docs_url():
    class NoFetch(base.Source):
        name = "nofetch"
        entity_type = ogprod.EntityType.WELL
        docs_url = "https://example.org/data"

        def parse(self, raw):
            raise NotImplementedError

    with pytest.raises(NotImplementedError, match="https://example.org/data"):
        NoFetch().fetch("/tmp")


def test_source_without_entities_raises_pointed_error():
    connector = ogprod.get("sodir")  # any registered source; base impl must raise
    if type(connector).parse_entities is base.Source.parse_entities:
        with pytest.raises(NotImplementedError, match="sodir"):
            connector.parse_entities("whatever.csv")
        with pytest.raises(NotImplementedError, match="sodir"):
            connector.fetch_entities("dest")
    else:
        pytest.skip("sodir implements entities; covered by its own tests")


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
    assert entities["field"].isna().all()  # optional metadata defaults to NA


def test_load_entities_dispatches_through_registry():
    with pytest.raises(KeyError, match="unknown source"):
        ogprod.load_entities("nope", "raw.csv")
    with pytest.raises(KeyError, match="unknown source"):
        ogprod.fetch_entities("nope", "dest")
