"""Tests for GeoParquet 1.1 preparation, column dropping, Hilbert sort, and bbox."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import shapely

from ogprod import geo, schema


def test_write_geoparquet_drops_unused_columns_and_writes_v11_bbox(tmp_path: Path) -> None:
    gdf = gpd.GeoDataFrame(
        {
            "source": ["brazil", "brazil", "brazil"],
            "facility_id": ["FPSO B", "FPSO A", "FPSO C"],
            "facility_name": ["FPSO B", "FPSO A", "FPSO C"],
            "oil_capacity_bpd": [180000.0, 225000.0, 150000.0],
            "unused_geoserver_junk": ["drop-me", "drop-me", "drop-me"],
        },
        geometry=[
            shapely.Point(-40.0, -22.0),
            shapely.Point(-42.5, -24.5),
            None,
        ],
        crs="EPSG:4326",
    )
    out = tmp_path / "facilities.parquet"
    geo.write_geoparquet(gdf, out, schema.GeoKind.FACILITIES)

    pf_schema = pq.read_schema(out)
    assert "unused_geoserver_junk" not in pf_schema.names
    assert "bbox" in pf_schema.names
    assert pf_schema.field("oil_capacity_bpd").type.equals("float32")
    assert b"geo" in pf_schema.metadata

    loaded = geo.read_geoparquet(out)
    assert len(loaded) == 3
    assert loaded.crs is not None
    assert loaded.crs.to_epsg() == 4326
    # Null geometry row is sorted after valid geometries.
    assert loaded.iloc[-1]["facility_id"] == "FPSO C"
    assert loaded.iloc[-1].geometry is None


def test_prepare_geo_repairs_invalid_polygon_and_reprojects() -> None:
    # Bowtie self-intersecting polygon in Web Mercator (EPSG:3857)
    bowtie = shapely.Polygon(
        [(-4700000, -2800000), (-4600000, -2700000), (-4700000, -2700000), (-4600000, -2800000)]
    )
    assert not bowtie.is_valid
    gdf = gpd.GeoDataFrame(
        {"source": ["brazil"], "field_id": ["TUPI"], "field_name": ["TUPI"]},
        geometry=[bowtie],
        crs="EPSG:3857",
    )
    prepared = geo.prepare_geo(gdf, schema.GeoKind.FIELDS)
    assert prepared.crs is not None
    assert prepared.crs.to_epsg() == 4326
    assert prepared.geometry.iloc[0].is_valid


def test_prepare_geo_strips_whitespace_and_nullifies_blank_strings() -> None:
    gdf = gpd.GeoDataFrame(
        {
            "source": ["brazil"],
            "well_id": ["W-1"],
            "well_name": [" W-1 \n"],
            "status": ["   "],
            "basin": ["Recôncavo\n"],
        },
        geometry=[shapely.Point(-38.5, -12.7)],
        crs="EPSG:4326",
    )
    prepared = geo.prepare_geo(gdf, schema.GeoKind.WELLS)
    assert prepared.loc[0, "well_name"] == "W-1"
    assert prepared.loc[0, "basin"] == "Recôncavo"
    assert prepared["status"].isna().iloc[0]
