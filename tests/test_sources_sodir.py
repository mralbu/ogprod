import json

import geopandas as gpd
import httpx2
import pandas as pd
import pytest
from shapely.geometry import Polygon

from ogprod import schema
from ogprod.sources import get


def test_parse_field_production_monthly(data_dir):
    frame = get("sodir").parse(data_dir / "sodir_field_production_monthly_sample.csv")
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


def test_parse_entity_id_survives_float_promoted_npdid(tmp_path):
    """If prfNpdidInformationCarrier ever arrives float-typed (e.g. because a
    null value in another row promotes the whole column to float64), the
    entity_id must still be the clean integer string "46437", not "46437.0".

    A bare ``.astype("string")`` on a float64 column would yield "46437.0",
    which silently breaks joins against entities keyed by "46437".
    """
    csv_path = tmp_path / "field_production_monthly.csv"
    csv_path.write_text(
        "prfInformationCarrier,prfYear,prfMonth,prfPrdOilNetMillSm3,"
        "prfPrdGasNetBillSm3,prfPrdNGLNetMillSm3,prfPrdCondensateNetMillSm3,"
        "prfPrdProducedWaterInFieldMillSm3,prfNpdidInformationCarrier\n"
        "TROLL,2024,5,0.6,3.6,0.32,0.04,0.9,46437\n"
        # Blank NPDID on this row forces the whole column to float64 when
        # pandas reads the CSV, promoting "46437" to 46437.0.
        "UNKNOWN FIELD,2024,5,0.1,0.1,0.01,0.01,0.1,\n"
    )
    frame = get("sodir").parse(csv_path)
    troll = frame[frame["entity_name"] == "TROLL"]
    assert set(troll["entity_id"]) == {"46437"}


def test_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.csv"
    bogus.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="Sodir"):
        get("sodir").parse(bogus)


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
    delineated) and must survive as a null-geometry row — an entity without
    a ring fence still carries joinable metadata. GHOST has a valid polygon
    but no NPDID and must be dropped (nothing can join it). SNORRE has a
    self-intersecting "bowtie" ring alongside a second, valid polygon,
    exercising ``.make_valid()`` ahead of the dissolve — without the repair,
    dissolving a bowtie with another polygon degrades into a
    GeometryCollection that fails schema validation.
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
                "GHOST",
            ],
            "fldNpdidField": ["26376286", "46437", "46437", "99999", "27438", "27438", None],
            "cmpLongName": ["Equinor Energy AS"] * 7,
            "fldMainArea": ["North sea"] * 7,
            "fldCurrentActivitySatus": [
                "Producing",
                "Producing",
                "Producing",
                "Approved",
                "Producing",
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
                Polygon([(5.0, 61.0), (5.1, 61.0), (5.1, 61.1), (5.0, 61.1)]),
            ],
        },
        crs="EPSG:4326",
    )
    path = tmp_path / "field.geojson"
    frame.to_file(path, driver="GeoJSON")
    return path


def test_parse_entities_field_ring_fences(field_area_geojson):
    entities = get("sodir").parse_entities(field_area_geojson)
    schema.validate_entities(entities)
    # JOHAN SVERDRUP, TROLL, SNORRE, NORDFELT — GHOST is dropped (no NPDID).
    assert len(entities) == 4
    assert set(entities["entity_type"]) == {"field"}

    troll = entities[entities["entity_name"] == "TROLL"].iloc[0]
    assert troll["entity_id"] == "46437"  # NPDID — joins to production
    assert troll.geometry.geom_type == "MultiPolygon"
    assert troll["field"] == "TROLL"
    assert troll["operator"] == "Equinor Energy AS"


def test_parse_entities_keeps_null_geometry_field(field_area_geojson):
    """A field whose only feature has a null geometry survives as a
    null-geometry row — it still joins production for metadata (the
    documented invariant: missing geometry is a row, not an error)."""
    entities = get("sodir").parse_entities(field_area_geojson)
    nordfelt = entities[entities["entity_name"] == "NORDFELT"].iloc[0]
    assert nordfelt["entity_id"] == "99999"
    assert nordfelt.geometry is None or pd.isna(nordfelt.geometry)


def test_parse_entities_drops_features_without_npdid(field_area_geojson):
    """A feature with geometry but no NPDID can't join anything; it is
    dropped rather than failing the whole parse."""
    entities = get("sodir").parse_entities(field_area_geojson)
    assert "GHOST" not in set(entities["entity_name"])


def test_parse_entities_repairs_self_intersecting_ring(field_area_geojson):
    """A field with one bowtie polygon and one valid polygon still dissolves
    into a single valid geometry.

    Without the ``.make_valid()`` repair in ``parse_entities``, dissolving
    the invalid bowtie ring together with the second polygon degrades into a
    GeometryCollection, which is neither Polygon nor MultiPolygon and is not
    ``is_valid`` in the way this test expects.
    """
    entities = get("sodir").parse_entities(field_area_geojson)
    schema.validate_entities(entities)

    snorre = entities[entities["entity_name"] == "SNORRE"].iloc[0]
    assert snorre["entity_id"] == "27438"
    assert snorre.geometry.geom_type in {"Polygon", "MultiPolygon"}
    assert snorre.geometry.is_valid


def test_parse_entities_id_survives_float_promoted_npdid(tmp_path):
    """Same float-promotion hazard as production parse, but for
    fldNpdidField: a null NPDID on one feature promotes the whole column to
    float64, and entity_id must still come out as "46437", not "46437.0".
    """
    frame = gpd.GeoDataFrame(
        {
            "fldName": ["TROLL", "NULLFIELD"],
            "fldNpdidField": [46437.0, None],
            "cmpLongName": ["Equinor Energy AS", "Equinor Energy AS"],
            "fldMainArea": ["North sea", "North sea"],
            "fldCurrentActivitySatus": ["Producing", "Approved"],
            "geometry": [
                Polygon([(3.5, 60.6), (3.6, 60.6), (3.6, 60.7), (3.5, 60.7)]),
                None,
            ],
        },
        crs="EPSG:4326",
    )
    path = tmp_path / "field.geojson"
    frame.to_file(path, driver="GeoJSON")

    entities = get("sodir").parse_entities(path)
    troll = entities[entities["entity_name"] == "TROLL"].iloc[0]
    assert troll["entity_id"] == "46437"


def test_parse_entities_rejects_other_files(tmp_path):
    bogus = tmp_path / "bogus.geojson"
    gpd.GeoDataFrame({"a": [1]}, geometry=[Polygon([(0, 0), (1, 0), (1, 1)])], crs=4326).to_file(
        bogus, driver="GeoJSON"
    )
    with pytest.raises(ValueError, match="Sodir"):
        get("sodir").parse_entities(bogus)


def test_fetch_entities_downloads_field_geojson(tmp_path):
    def handler(request):
        assert "MapServer/7100/query" in str(request.url)
        return httpx2.Response(
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

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    paths = get("sodir").fetch_entities(tmp_path, client=client)
    assert len(paths) == 1
    with paths[0].open() as fh:
        payload = json.load(fh)
    assert payload["type"] == "FeatureCollection"
