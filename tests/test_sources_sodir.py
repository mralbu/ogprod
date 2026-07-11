import json

import geopandas as gpd
import httpx
import pandas as pd
import pytest
from shapely.geometry import Polygon

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


@pytest.fixture
def field_area_geojson(tmp_path):
    """A GeoJSON fixture mirroring Sodir FactMaps' field-outline attributes.

    Column names mirror the live ArcGIS DataService (layer 7100, "field")
    confirmed during Task 7's live verification: ``fldName``,
    ``fldNpdidField``, ``cmpLongName``, ``fldMainArea``,
    ``fldCurrentActivitySatus``. GeoJSON is used (rather than a shapefile)
    because that is the actual live download format — the documented
    shapefile zip 404s — and because shapefile DBF fields truncate to 10
    characters, which would mangle these longer column names. TROLL has two
    polygons to exercise the dissolve into one MultiPolygon per field.

    NORDFELT carries only a null geometry (e.g. newly approved, not yet
    delineated) and must be dropped entirely, exercising the null-geometry
    drop in ``parse_entities``. SNORRE has a self-intersecting "bowtie" ring
    alongside a second, valid polygon, exercising ``.make_valid()`` ahead of
    the dissolve — without the repair, dissolving a bowtie with another
    polygon degrades into a GeometryCollection that fails schema validation.
    """
    frame = gpd.GeoDataFrame(
        {
            "fldName": [
                "JOHAN SVERDRUP",
                "TROLL",
                "TROLL",
                "NORDFELT",
                "SNORRE",
                "SNORRE",
            ],
            "fldNpdidField": ["26376286", "46437", "46437", "99999", "27438", "27438"],
            "cmpLongName": [
                "Equinor Energy AS",
                "Equinor Energy AS",
                "Equinor Energy AS",
                "Equinor Energy AS",
                "Equinor Energy AS",
                "Equinor Energy AS",
            ],
            "fldMainArea": [
                "North sea",
                "North sea",
                "North sea",
                "North sea",
                "North sea",
                "North sea",
            ],
            "fldCurrentActivitySatus": [
                "Producing",
                "Producing",
                "Producing",
                "Approved",
                "Producing",
                "Producing",
            ],
            "geometry": [
                Polygon([(2.6, 58.8), (2.7, 58.8), (2.7, 58.9), (2.6, 58.9)]),
                Polygon([(3.5, 60.6), (3.6, 60.6), (3.6, 60.7), (3.5, 60.7)]),
                Polygon([(3.7, 60.6), (3.8, 60.6), (3.8, 60.7), (3.7, 60.7)]),
                None,
                # Self-intersecting "bowtie" ring (invalid until repaired).
                Polygon([(4.0, 61.0), (4.1, 61.1), (4.1, 61.0), (4.0, 61.1), (4.0, 61.0)]),
                Polygon([(4.2, 61.0), (4.3, 61.0), (4.3, 61.1), (4.2, 61.1)]),
            ],
        },
        crs="EPSG:4326",
    )
    path = tmp_path / "field.geojson"
    frame.to_file(path, driver="GeoJSON")
    return path


def test_parse_entities_field_ring_fences(field_area_geojson):
    entities = ogprod.load_entities("sodir", field_area_geojson)
    schema.validate_entities(entities)
    # JOHAN SVERDRUP, TROLL, SNORRE — NORDFELT is dropped (null geometry only).
    assert len(entities) == 3
    assert set(entities["entity_type"]) == {"field"}

    troll = entities[entities["entity_name"] == "TROLL"].iloc[0]
    assert troll["entity_id"] == "46437"  # NPDID — joins to production
    assert troll.geometry.geom_type == "MultiPolygon"
    assert troll["field"] == "TROLL"
    assert troll["operator"] == "Equinor Energy AS"


def test_parse_entities_drops_null_geometry_field(field_area_geojson):
    """A field whose only feature has a null geometry is dropped entirely.

    Without the ``frame.geometry.isna()`` filter in ``parse_entities``, this
    row would reach ``dissolve`` and either raise or produce a null geometry
    that fails ``schema.validate_entities``.
    """
    entities = ogprod.load_entities("sodir", field_area_geojson)
    assert "NORDFELT" not in set(entities["entity_name"])


def test_parse_entities_repairs_self_intersecting_ring(field_area_geojson):
    """A field with one bowtie polygon and one valid polygon still dissolves
    into a single valid geometry.

    Without the ``.make_valid()`` repair in ``parse_entities``, dissolving
    the invalid bowtie ring together with the second polygon degrades into a
    GeometryCollection, which is neither Polygon nor MultiPolygon and is not
    ``is_valid`` in the way this test expects.
    """
    entities = ogprod.load_entities("sodir", field_area_geojson)
    schema.validate_entities(entities)

    snorre = entities[entities["entity_name"] == "SNORRE"].iloc[0]
    assert snorre["entity_id"] == "27438"
    assert snorre.geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert snorre.geometry.is_valid


def test_parse_entities_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.geojson"
    gpd.GeoDataFrame({"a": [1]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs=4326).to_file(
        bogus, driver="GeoJSON"
    )
    with pytest.raises(ValueError, match="Sodir"):
        ogprod.load_entities("sodir", bogus)


def test_fetch_entities_downloads_field_geojson(tmp_path):
    def handler(request):
        assert "MapServer/7100/query" in str(request.url)
        return httpx.Response(
            200,
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"fldName": "TROLL", "fldNpdidField": "46437"},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[3.5, 60.6], [3.6, 60.6], [3.6, 60.7], [3.5, 60.6]]],
                        },
                    }
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    paths = ogprod.fetch_entities("sodir", tmp_path, client=client)
    assert len(paths) == 1
    with paths[0].open() as fh:
        payload = json.load(fh)
    assert payload["type"] == "FeatureCollection"
