"""Compact GeoParquet 1.1 preparation, Hilbert spatial sorting, and atomic I/O."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from ogprod.schema import GEO_CRS, GEO_SPECS, GeoKind, validate_geo


def _cast_geo_columns(gdf: gpd.GeoDataFrame, kind: GeoKind) -> gpd.GeoDataFrame:
    """Drop unused columns, fill missing optional columns, and apply compact dtypes."""
    spec = GEO_SPECS[kind]
    out = gdf.copy()
    for col in spec.columns:
        if col == "geometry":
            continue
        if col not in out.columns:
            out[col] = pd.NA
        if col in spec.float32_columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("float32")
        elif col in spec.int32_columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int32")
        else:
            cleaned = out[col].astype("string").str.strip()
            out[col] = cleaned.mask(cleaned == "", pd.NA)
    return gpd.GeoDataFrame(out[list(spec.columns)], geometry="geometry", crs=out.crs)


def _repair_and_reproject(gdf: gpd.GeoDataFrame, crs: str | None) -> gpd.GeoDataFrame:
    """Ensure geometries are in EPSG:4326 and topologically valid."""
    out = gdf.copy()
    if crs is not None and out.crs is None:
        out = out.set_crs(crs)
    if out.crs is None:
        out = out.set_crs(GEO_CRS)
    elif out.crs.to_epsg() != 4326:
        out = out.to_crs(GEO_CRS)

    non_null_mask = out.geometry.notna() & ~out.geometry.is_empty
    if non_null_mask.any():
        invalid_mask = non_null_mask & ~out.geometry.is_valid
        if invalid_mask.any():
            out.loc[invalid_mask, "geometry"] = out.loc[invalid_mask, "geometry"].make_valid()
    return out


def _hilbert_sort(gdf: gpd.GeoDataFrame, id_column: str) -> gpd.GeoDataFrame:
    """Sort rows with geometry along a Hilbert space-filling curve; append nulls."""
    if len(gdf) <= 1:
        return gpd.GeoDataFrame(gdf.reset_index(drop=True), geometry="geometry", crs=GEO_CRS)
    has_geom = gdf.geometry.notna() & ~gdf.geometry.is_empty
    if not has_geom.any():
        sorted_df = gdf.sort_values(id_column, ignore_index=True)
        return gpd.GeoDataFrame(sorted_df, geometry="geometry", crs=GEO_CRS)

    with_geom = gdf.loc[has_geom].copy()
    without_geom = gdf.loc[~has_geom].sort_values(id_column)
    order = with_geom.hilbert_distance().argsort(kind="mergesort")
    sorted_geom = with_geom.iloc[order]
    if len(without_geom) == 0:
        return gpd.GeoDataFrame(
            sorted_geom.reset_index(drop=True), geometry="geometry", crs=GEO_CRS
        )
    combined = pd.concat([sorted_geom, without_geom], ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=GEO_CRS)


def prepare_geo(
    gdf: gpd.GeoDataFrame,
    kind: GeoKind,
    *,
    crs: str | None = None,
) -> gpd.GeoDataFrame:
    """Project strictly canonical columns, reproject to EPSG:4326, repair, validate, and sort."""
    spec = GEO_SPECS[kind]
    cleaned = _repair_and_reproject(gdf, crs)
    projected = _cast_geo_columns(cleaned, kind)
    validated = validate_geo(projected, kind)
    return _hilbert_sort(validated, spec.id_column)


def write_geoparquet(
    gdf: gpd.GeoDataFrame,
    path: Path,
    kind: GeoKind,
    *,
    crs: str | None = None,
) -> Path:
    """Atomically write a Hilbert-sorted, ZSTD-compressed GeoParquet 1.1 file with bbox."""
    prepared = prepare_geo(gdf, kind, crs=crs)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    prepared.to_parquet(
        tmp,
        index=False,
        schema_version="1.1.0",
        write_covering_bbox=True,
        compression="zstd",
        compression_level=6,
    )
    tmp.replace(path)
    return path


def read_geoparquet(path: Path) -> gpd.GeoDataFrame:
    """Read a GeoParquet file back into a GeoDataFrame."""
    return gpd.read_parquet(path)
