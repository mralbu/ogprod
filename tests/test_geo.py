"""Tests for GeoParquet 1.1 preparation, column dropping, Hilbert sort, and bbox."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

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
    assert loaded.iloc[-1]["facility_id"] == "FPSO C"
    assert loaded.iloc[-1].geometry is None


def test_prepare_geo_repairs_invalid_polygon_and_reprojects() -> None:
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


@settings(deadline=None, max_examples=25)
@given(
    split=st.floats(min_value=0.05, max_value=0.95, allow_nan=False, allow_infinity=False).filter(
        lambda v: abs(v - 0.5) > 0.02
    )
)
def test_assign_by_largest_overlap_picks_majority_region(split: float) -> None:
    # Two adjacent rectangles covering [0, split] and [split, 1] inside a target covering [0, 1].
    regions = gpd.GeoDataFrame(
        {"label": ["WEST", "EAST"]},
        geometry=[
            shapely.box(-95.0, 19.0, -95.0 + split, 20.0),
            shapely.box(-95.0 + split, 19.0, -94.0, 20.0),
        ],
        crs="EPSG:4326",
    )
    targets = gpd.GeoDataFrame(
        {"fid": ["F1"]},
        geometry=[shapely.box(-95.0, 19.2, -94.0, 19.8)],
        crs="EPSG:4326",
    )
    assigned = geo.assign_by_largest_overlap(
        targets, regions, id_column="fid", label_column="label", equal_area_crs="EPSG:6372"
    )
    expected = "WEST" if split > 0.5 else "EAST"
    assert assigned.iloc[0] == expected


def test_assign_by_largest_overlap_sums_multi_part_regions_and_handles_nulls() -> None:
    regions = gpd.GeoDataFrame(
        {"label": ["SPLIT", "SPLIT", "SINGLE", "   "]},
        geometry=[
            shapely.box(-94.0, 19.0, -93.65, 20.0),
            shapely.box(-93.35, 19.0, -93.0, 20.0),
            shapely.box(-93.65, 19.0, -93.35, 20.0),
            shapely.box(-94.0, 19.0, -93.0, 20.0),
        ],
        crs="EPSG:4326",
    )
    targets = gpd.GeoDataFrame(
        {"fid": ["OVERLAP", "OUTSIDE", "NO_GEOM"]},
        geometry=[
            shapely.box(-94.0, 19.1, -93.0, 19.9),
            shapely.box(-100.0, 25.0, -99.5, 25.5),
            None,
        ],
        crs="EPSG:4326",
    )
    assigned = geo.assign_by_largest_overlap(
        targets, regions, id_column="fid", label_column="label", equal_area_crs="EPSG:6372"
    )
    assert assigned.iloc[0] == "SPLIT"
    assert pd.isna(assigned.iloc[1])
    assert pd.isna(assigned.iloc[2])
