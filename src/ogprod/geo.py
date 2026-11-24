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


def _prepare_overlay_frame(
    gdf: gpd.GeoDataFrame,
    column: str,
    rename_to: str,
    equal_area_crs: str,
    *,
    drop_blank: bool = False,
) -> gpd.GeoDataFrame:
    has_geom = gdf.geometry.notna() & ~gdf.geometry.is_empty
    if drop_blank:
        vals = gdf[column].astype("string").str.strip()
        keep = has_geom & vals.notna() & (vals != "")
    else:
        vals = gdf[column]
        keep = has_geom & vals.notna()
    sub = gpd.GeoDataFrame(
        {rename_to: vals.loc[keep]},
        geometry=gdf.geometry.loc[keep],
        crs=gdf.crs or GEO_CRS,
    )
    if len(sub) == 0:
        return sub
    invalid = ~sub.geometry.is_valid
    if invalid.any():
        sub.loc[invalid, "geometry"] = sub.loc[invalid, "geometry"].make_valid()
    return sub.to_crs(equal_area_crs)


def assign_by_largest_overlap(
    targets: gpd.GeoDataFrame,
    regions: gpd.GeoDataFrame,
    *,
    id_column: str,
    label_column: str,
    equal_area_crs: str,
) -> pd.Series:
    """Assign each target polygon the label of the region with largest summed overlap area.

    Returns a ``string`` Series aligned with ``targets.index`` (``pd.NA`` when a target has
    null/empty geometry or does not overlap any region). Ties break alphabetically by label.
    """
    empty = pd.Series([pd.NA] * len(targets), index=targets.index, dtype="string")
    if len(targets) == 0 or len(regions) == 0:
        return empty

    t_proj = _prepare_overlay_frame(targets, id_column, "_target_id", equal_area_crs)
    r_proj = _prepare_overlay_frame(
        regions, label_column, "_region_label", equal_area_crs, drop_blank=True
    )
    if len(t_proj) == 0 or len(r_proj) == 0:
        return empty

    inter = gpd.overlay(t_proj, r_proj, how="intersection", keep_geom_type=False)
    if len(inter) == 0:
        return empty

    inter = inter.assign(_area=inter.geometry.area)
    inter = inter.loc[inter["_area"] > 0]
    if len(inter) == 0:
        return empty

    totals = inter.groupby(["_target_id", "_region_label"], as_index=False, sort=False)[
        "_area"
    ].sum()
    totals = totals.sort_values(
        ["_target_id", "_area", "_region_label"],
        ascending=[True, False, True],
        kind="mergesort",
    )
    winners = totals.drop_duplicates(subset=["_target_id"], keep="first")
    lookup = dict(zip(winners["_target_id"], winners["_region_label"], strict=True))
    return targets[id_column].map(lookup).astype("string")
