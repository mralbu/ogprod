"""Layer-2 web visualization exporter (rollups, search index, meta.json, PMTiles).

Reads canonical Layer-1 Parquet & GeoParquet 1.1 files from :class:`ogprod.Store`
(optionally syncing from the Hugging Face mirror first) and produces static web
assets ready for the MapLibre GL JS + ECharts + lazy DuckDB-WASM explorer:

- ``meta.json``: source attribution, bounding boxes, years, freshness, mirror URL, gaps.
- ``rollups/``: Parquet and compact JSON monthly aggregations (country, basin, field,
  facility with FPSO utilization ``actual_oil_bpd / oil_capacity_bpd``).
- ``search/index.json``: compact lazy-loaded index for fields, facilities, and wells.
- ``tiles/ogprod.pmtiles``: multi-layer vector tiles built via Felt's ``tippecanoe``.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import geopandas as gpd
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import shapely

from ogprod import mirror, schema, sources, store
from ogprod.sources import brazil

_LICENCES: dict[str, str] = {
    "brazil": "Public open data — ANP (Agência Nacional do Petróleo)",
    "norway": "NLOD — Sodir (Norwegian Offshore Directorate)",
    "mexico": "Datos Abiertos — SENER / CNIH SIH",
}

# Synced with site/src/lib/mapStyle.ts (MAP_LAYER_MIN_ZOOM).
_TILE_MIN_ZOOM = 0
_TILE_MAX_ZOOM = 12
_TILE_LAYER_MIN_ZOOM: dict[str, int] = {
    "fields": 0,
    "field_centroids": 6,
    "facilities": 5,
    "wells": 8,
}

_COUNTRY_MONTHLY_SQL = """
SELECT source,
       period,
       SUM(CAST(oil_prod AS DOUBLE)) AS oil_prod,
       SUM(CAST(condensate_prod AS DOUBLE)) AS condensate_prod,
       SUM(CAST(ngl_prod AS DOUBLE)) AS ngl_prod,
       SUM(CAST(gas_prod AS DOUBLE)) AS gas_prod,
       SUM(CAST(water_prod AS DOUBLE)) AS water_prod,
       SUM(CAST(water_inj AS DOUBLE)) AS water_inj,
       SUM(CAST(gas_inj AS DOUBLE)) AS gas_inj,
       SUM(CAST(co2_inj AS DOUBLE)) AS co2_inj
FROM production
GROUP BY source, period
ORDER BY source, period
"""

_BASIN_MONTHLY_SQL = """
SELECT source,
       coalesce(nullif(trim(basin), ''), 'Unassigned') AS basin,
       period,
       SUM(CAST(oil_prod AS DOUBLE)) AS oil_prod,
       SUM(CAST(condensate_prod AS DOUBLE)) AS condensate_prod,
       SUM(CAST(ngl_prod AS DOUBLE)) AS ngl_prod,
       SUM(CAST(gas_prod AS DOUBLE)) AS gas_prod,
       SUM(CAST(water_prod AS DOUBLE)) AS water_prod,
       SUM(CAST(water_inj AS DOUBLE)) AS water_inj,
       SUM(CAST(gas_inj AS DOUBLE)) AS gas_inj,
       SUM(CAST(co2_inj AS DOUBLE)) AS co2_inj
FROM production
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
"""

_FIELD_MONTHLY_SQL = """
SELECT p.source,
       p.field_id,
       max(p.field_name) AS field_name,
       p.period,
       SUM(CAST(p.oil_prod AS DOUBLE)) AS oil_prod,
       SUM(CAST(p.condensate_prod AS DOUBLE)) AS condensate_prod,
       SUM(CAST(p.ngl_prod AS DOUBLE)) AS ngl_prod,
       SUM(CAST(p.gas_prod AS DOUBLE)) AS gas_prod,
       SUM(CAST(p.water_prod AS DOUBLE)) AS water_prod,
       SUM(CAST(p.water_inj AS DOUBLE)) AS water_inj,
       SUM(CAST(p.gas_inj AS DOUBLE)) AS gas_inj,
       SUM(CAST(p.co2_inj AS DOUBLE)) AS co2_inj
FROM production p
WHERE p.field_id <> ''
GROUP BY p.source, p.field_id, p.period
ORDER BY p.source, p.field_id, p.period
"""

_FACILITY_MONTHLY_SQL = """
SELECT p.source,
       p.facility_id,
       max(p.facility_name) AS facility_name,
       p.period,
       SUM(CAST(p.oil_prod AS DOUBLE)) AS oil_prod,
       SUM(CAST(p.condensate_prod AS DOUBLE)) AS condensate_prod,
       SUM(CAST(p.ngl_prod AS DOUBLE)) AS ngl_prod,
       SUM(CAST(p.gas_prod AS DOUBLE)) AS gas_prod,
       SUM(CAST(p.water_prod AS DOUBLE)) AS water_prod,
       SUM(CAST(p.water_inj AS DOUBLE)) AS water_inj,
       SUM(CAST(p.gas_inj AS DOUBLE)) AS gas_inj,
       SUM(CAST(p.co2_inj AS DOUBLE)) AS co2_inj,
       max(CAST(f.oil_capacity_bpd AS DOUBLE)) AS oil_capacity_bpd,
       max(CAST(f.gas_capacity_e3m3d AS DOUBLE)) AS gas_capacity_e3m3d,
       CASE WHEN max(CAST(f.oil_capacity_bpd AS DOUBLE)) > 0
            THEN SUM(CAST(p.oil_prod AS DOUBLE)) / max(CAST(f.oil_capacity_bpd AS DOUBLE))
            ELSE NULL END AS oil_utilization
FROM production p
LEFT JOIN facilities f
       ON p.source = f.source AND p.facility_id = f.facility_id
WHERE p.facility_id <> ''
GROUP BY p.source, p.facility_id, p.period
ORDER BY p.source, p.facility_id, p.period
"""

_FIELD_STATS_SQL = """
WITH monthly AS (
    SELECT source, field_id AS eid, period,
           SUM(CAST(oil_prod AS DOUBLE)) AS oil_bpd,
           SUM(CAST(gas_prod AS DOUBLE)) AS gas_e3m3d
    FROM production WHERE field_id <> ''
    GROUP BY source, field_id, period
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY source, eid ORDER BY period DESC) AS rn
    FROM monthly
)
SELECT source, eid,
       min(year(period)) AS first_year,
       max(year(period)) AS last_year,
       max(period) AS latest_period,
       SUM(coalesce(oil_bpd, 0.0) * day(last_day(period))) / 1e6 AS cum_oil_mmbbl,
       max(CASE WHEN rn = 1 THEN oil_bpd END) AS latest_oil_bpd,
       max(CASE WHEN rn = 1 THEN gas_e3m3d END) AS latest_gas_e3m3d
FROM ranked GROUP BY source, eid
"""

_FACILITY_STATS_SQL = """
WITH monthly AS (
    SELECT source, facility_id AS eid, period,
           SUM(CAST(oil_prod AS DOUBLE)) AS oil_bpd,
           SUM(CAST(gas_prod AS DOUBLE)) AS gas_e3m3d
    FROM production WHERE facility_id <> ''
    GROUP BY source, facility_id, period
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY source, eid ORDER BY period DESC) AS rn
    FROM monthly
)
SELECT source, eid,
       min(year(period)) AS first_year,
       max(year(period)) AS last_year,
       max(period) AS latest_period,
       SUM(coalesce(oil_bpd, 0.0) * day(last_day(period))) / 1e6 AS cum_oil_mmbbl,
       max(CASE WHEN rn = 1 THEN oil_bpd END) AS latest_oil_bpd,
       max(CASE WHEN rn = 1 THEN gas_e3m3d END) AS latest_gas_e3m3d
FROM ranked GROUP BY source, eid
"""

_WELL_STATS_SQL = """
WITH monthly AS (
    SELECT source, entity_id AS eid, period,
           SUM(CAST(oil_prod AS DOUBLE)) AS oil_bpd,
           SUM(CAST(gas_prod AS DOUBLE)) AS gas_e3m3d
    FROM production WHERE entity_type = 'well' AND entity_id <> ''
    GROUP BY source, entity_id, period
),
ranked AS (
    SELECT *, row_number() OVER (PARTITION BY source, eid ORDER BY period DESC) AS rn
    FROM monthly
)
SELECT source, eid,
       min(year(period)) AS first_year,
       max(year(period)) AS last_year,
       max(period) AS latest_period,
       SUM(coalesce(oil_bpd, 0.0) * day(last_day(period))) / 1e6 AS cum_oil_mmbbl,
       max(CASE WHEN rn = 1 THEN oil_bpd END) AS latest_oil_bpd,
       max(CASE WHEN rn = 1 THEN gas_e3m3d END) AS latest_gas_e3m3d
FROM ranked GROUP BY source, eid
"""

_PRODUCT_COUNTS_SQL = """
SELECT count(oil_prod),
       count(condensate_prod),
       count(ngl_prod),
       count(gas_prod),
       count(water_prod),
       count(water_inj),
       count(gas_inj),
       count(co2_inj)
FROM production
WHERE source = ?
"""


@dataclass(frozen=True)
class ExportSummary:
    """Summary of one Layer-2 web export run."""

    out_dir: Path
    sources: tuple[str, ...]
    search_entries: int
    has_tiles: bool


def _atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def _atomic_write_df_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, tmp, compression="zstd", compression_level=6)
    tmp.replace(path)


def _round_opt(value: Any, digits: int = 2) -> float | None:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return None
    if pd.isna(value):
        return None
    return round(float(value), digits)


def _str_opt(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _int_opt(value: Any) -> int | None:
    if value is None or pd.isna(value):
        return None
    return int(value)


def _known_gaps_for_source(source_name: str) -> list[dict[str, Any]]:
    if source_name == "brazil":
        return [
            {
                "column": g.column,
                "start": g.start.isoformat(),
                "end": g.end.isoformat(),
                "description": g.description,
                "resolved_in_country_rollup": g.resolved_in_country_rollup,
                **(
                    {"country_rollup_source": g.country_rollup_source}
                    if g.country_rollup_source
                    else {}
                ),
            }
            for g in brazil.KNOWN_GAPS
        ]
    return []


def _apply_country_gap_backfills(country_df: pd.DataFrame) -> pd.DataFrame:
    """Splice official ANP national gas rates into Brazil's country rollup for 2003-07..2005-06."""
    if country_df.empty or "gas_prod" not in country_df.columns:
        return country_df
    rates = brazil.national_gas_gap_rates()
    periods = pd.to_datetime(country_df["period"]).dt.date
    replacements = periods.map(rates)
    mask = (country_df["source"] == "brazil") & replacements.notna()
    if not mask.any():
        return country_df
    out = country_df.copy()
    out.loc[mask, "gas_prod"] = replacements.loc[mask].astype("float64")
    return out


def _mask_basin_known_gaps(basin_df: pd.DataFrame) -> pd.DataFrame:
    """Mask incomplete months in Brazil basin rollups so charts show a break instead of ~5%."""
    if basin_df.empty:
        return basin_df
    out = basin_df
    periods = pd.to_datetime(basin_df["period"]).dt.date
    is_br = basin_df["source"] == "brazil"
    for gap in brazil.KNOWN_GAPS:
        if gap.column not in out.columns:
            continue
        mask = is_br & (periods >= gap.start) & (periods <= gap.end)
        if mask.any():
            if out is basin_df:
                out = basin_df.copy()
            out.loc[mask, gap.column] = float("nan")
    return out


def _row_to_monthly_point(row: dict[str, Any]) -> dict[str, Any]:
    period = row["period"]
    p_str = period.strftime("%Y-%m-%d") if hasattr(period, "strftime") else str(period)[:10]
    point: dict[str, Any] = {"p": p_str}
    for col in schema.RATE_COLUMNS:
        val = _round_opt(row.get(col), 2)
        if val is not None and val != 0.0:
            point[col] = val
    return point


def _export_country_and_basin_rollups(
    con: duckdb.DuckDBPyConnection,
    rollups_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    country_df = _apply_country_gap_backfills(con.execute(_COUNTRY_MONTHLY_SQL).df())
    _atomic_write_df_parquet(country_df, rollups_dir / "country_monthly.parquet")

    country_json: dict[str, list[dict[str, Any]]] = {}
    for rec in country_df.to_dict("records"):
        src = str(rec["source"])
        country_json.setdefault(src, []).append(_row_to_monthly_point(rec))
    _atomic_write_json(rollups_dir / "country_monthly.json", country_json)

    basin_df = _mask_basin_known_gaps(con.execute(_BASIN_MONTHLY_SQL).df())
    _atomic_write_df_parquet(basin_df, rollups_dir / "basin_monthly.parquet")

    basin_json: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for rec in basin_df.to_dict("records"):
        src = str(rec["source"])
        basin = str(rec["basin"])
        basin_json.setdefault(src, {}).setdefault(basin, []).append(_row_to_monthly_point(rec))
    _atomic_write_json(rollups_dir / "basin_monthly.json", basin_json)
    return country_df, basin_df


def _export_entity_monthly_rollups(
    con: duckdb.DuckDBPyConnection,
    rollups_dir: Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    field_df = con.execute(_FIELD_MONTHLY_SQL).df()
    _atomic_write_df_parquet(field_df, rollups_dir / "field_monthly.parquet")
    _write_sharded_monthly(field_df, rollups_dir / "field_monthly", id_col="field_id")

    fac_df = con.execute(_FACILITY_MONTHLY_SQL).df()
    _atomic_write_df_parquet(fac_df, rollups_dir / "facility_monthly.parquet")
    _write_sharded_monthly(fac_df, rollups_dir / "facility_monthly", id_col="facility_id")
    return field_df, fac_df


def _write_sharded_monthly(df: pd.DataFrame, target_dir: Path, *, id_col: str) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    if df.empty:
        return
    by_source: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for rec in df.to_dict("records"):
        src = str(rec["source"])
        entity_id = str(rec[id_col])
        point = _row_to_monthly_point(rec)
        if "oil_utilization" in rec:
            util = _round_opt(rec.get("oil_utilization"), 4)
            if util is not None:
                point["oil_util"] = util
        by_source.setdefault(src, {}).setdefault(entity_id, []).append(point)
    for src, payload in sorted(by_source.items()):
        _atomic_write_json(target_dir / f"{src}.json", payload)


def _load_entity_stats(
    con: duckdb.DuckDBPyConnection,
) -> dict[str, dict[tuple[str, str], dict[str, Any]]]:
    queries = {
        "fields": _FIELD_STATS_SQL,
        "facilities": _FACILITY_STATS_SQL,
        "wells": _WELL_STATS_SQL,
    }
    stats: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    for kind, query in queries.items():
        df = con.execute(query).df()
        stats[kind] = {(str(rec["source"]), str(rec["eid"])): rec for rec in df.to_dict("records")}
    return stats


def _has_valid_geom(geom: Any) -> bool:
    return isinstance(geom, shapely.Geometry) and not geom.is_empty


def _geom_lon_lat(geom: Any) -> tuple[float | None, float | None]:
    if not _has_valid_geom(geom):
        return None, None
    pt = geom if geom.geom_type == "Point" else geom.representative_point()
    return _round_opt(pt.x, 5), _round_opt(pt.y, 5)


def _build_fields_summary(
    fields_gdf: gpd.GeoDataFrame,
    field_stats: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for _, row in fields_gdf.iterrows():
        key = (str(row["source"]), str(row["field_id"]))
        seen.add(key)
        st = field_stats.get(key, {})
        lon, lat = _geom_lon_lat(row.geometry)
        latest_p = st.get("latest_period")
        rows.append(
            {
                "source": key[0],
                "field_id": key[1],
                "field_name": _str_opt(row.get("field_name")) or key[1],
                "basin": _str_opt(row.get("basin")),
                "region": _str_opt(row.get("region")),
                "environment": _str_opt(row.get("environment")),
                "operator": _str_opt(row.get("operator")),
                "status": _str_opt(row.get("status")),
                "water_depth_m": _round_opt(row.get("water_depth_m"), 1),
                "area_km2": _round_opt(row.get("area_km2"), 2),
                "first_year": _int_opt(st.get("first_year")),
                "last_year": _int_opt(st.get("last_year")),
                "latest_period": latest_p.strftime("%Y-%m-%d")
                if hasattr(latest_p, "strftime")
                else None,
                "cum_oil_mmbbl": _round_opt(st.get("cum_oil_mmbbl"), 3),
                "latest_oil_bpd": _round_opt(st.get("latest_oil_bpd"), 1),
                "latest_gas_e3m3d": _round_opt(st.get("latest_gas_e3m3d"), 1),
                "lon": lon,
                "lat": lat,
            }
        )
    for (src, fid), st in sorted(field_stats.items()):
        if (src, fid) in seen:
            continue
        latest_p = st.get("latest_period")
        rows.append(
            {
                "source": src,
                "field_id": fid,
                "field_name": fid,
                "basin": None,
                "region": None,
                "environment": None,
                "operator": None,
                "status": None,
                "water_depth_m": None,
                "area_km2": None,
                "first_year": _int_opt(st.get("first_year")),
                "last_year": _int_opt(st.get("last_year")),
                "latest_period": latest_p.strftime("%Y-%m-%d")
                if hasattr(latest_p, "strftime")
                else None,
                "cum_oil_mmbbl": _round_opt(st.get("cum_oil_mmbbl"), 3),
                "latest_oil_bpd": _round_opt(st.get("latest_oil_bpd"), 1),
                "latest_gas_e3m3d": _round_opt(st.get("latest_gas_e3m3d"), 1),
                "lon": None,
                "lat": None,
            }
        )
    rows.sort(key=lambda r: -(r["latest_oil_bpd"] or 0.0))
    return rows


def _build_facilities_summary(
    fac_gdf: gpd.GeoDataFrame,
    fac_stats: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in fac_gdf.iterrows():
        key = (str(row["source"]), str(row["facility_id"]))
        st = fac_stats.get(key, {})
        lon, lat = _geom_lon_lat(row.geometry)
        oil_cap = _round_opt(row.get("oil_capacity_bpd"), 1)
        gas_cap = _round_opt(row.get("gas_capacity_e3m3d"), 1)
        latest_oil = _round_opt(st.get("latest_oil_bpd"), 1)
        latest_gas = _round_opt(st.get("latest_gas_e3m3d"), 1)
        oil_util = (
            round(latest_oil / oil_cap, 4)
            if oil_cap and oil_cap > 0 and latest_oil is not None
            else None
        )
        gas_util = (
            round(latest_gas / gas_cap, 4)
            if gas_cap and gas_cap > 0 and latest_gas is not None
            else None
        )
        latest_p = st.get("latest_period")
        rows.append(
            {
                "source": key[0],
                "facility_id": key[1],
                "facility_name": _str_opt(row.get("facility_name")) or key[1],
                "facility_type": _str_opt(row.get("facility_type")),
                "fields": _str_opt(row.get("fields")),
                "basin": _str_opt(row.get("basin")),
                "operator": _str_opt(row.get("operator")),
                "status": _str_opt(row.get("status")),
                "start_year": _int_opt(row.get("start_year")),
                "water_depth_m": _round_opt(row.get("water_depth_m"), 1),
                "oil_capacity_bpd": oil_cap,
                "gas_capacity_e3m3d": gas_cap,
                "first_year": _int_opt(st.get("first_year")),
                "last_year": _int_opt(st.get("last_year")),
                "latest_period": latest_p.strftime("%Y-%m-%d")
                if hasattr(latest_p, "strftime")
                else None,
                "latest_oil_bpd": latest_oil,
                "latest_gas_e3m3d": latest_gas,
                "oil_utilization": oil_util,
                "gas_utilization": gas_util,
                "lon": lon,
                "lat": lat,
            }
        )
    rows.sort(key=lambda r: -(r["latest_oil_bpd"] or r["oil_capacity_bpd"] or 0.0))
    return rows


def _compact_entry(base_entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in base_entry.items() if v is not None}


def _build_search_index(
    fields_summary: list[dict[str, Any]],
    facilities_summary: list[dict[str, Any]],
    wells_gdf: gpd.GeoDataFrame,
    well_stats: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for f in fields_summary:
        entries.append(
            _compact_entry(
                {
                    "t": "field",
                    "s": f["source"],
                    "id": f["field_id"],
                    "n": f["field_name"],
                    "b": f["basin"],
                    "lon": f["lon"],
                    "lat": f["lat"],
                    "fy": f["first_year"],
                    "ly": f["last_year"],
                    "lo": f["latest_oil_bpd"],
                }
            )
        )
    for fac in facilities_summary:
        entries.append(
            _compact_entry(
                {
                    "t": "facility",
                    "s": fac["source"],
                    "id": fac["facility_id"],
                    "n": fac["facility_name"],
                    "f": fac["fields"],
                    "b": fac["basin"],
                    "lon": fac["lon"],
                    "lat": fac["lat"],
                    "fy": fac["first_year"],
                    "ly": fac["last_year"],
                    "lo": fac["latest_oil_bpd"],
                    "cap": fac["oil_capacity_bpd"],
                }
            )
        )
    for _, row in wells_gdf.iterrows():
        key = (str(row["source"]), str(row["well_id"]))
        st = well_stats.get(key, {})
        lon, lat = _geom_lon_lat(row.geometry)
        entries.append(
            _compact_entry(
                {
                    "t": "well",
                    "s": key[0],
                    "id": key[1],
                    "n": _str_opt(row.get("well_name")) or key[1],
                    "f": _str_opt(row.get("field_name")),
                    "b": _str_opt(row.get("basin")),
                    "lon": lon,
                    "lat": lat,
                    "fy": _int_opt(st.get("first_year")),
                    "ly": _int_opt(st.get("last_year")),
                    "lo": _round_opt(st.get("latest_oil_bpd"), 1),
                }
            )
        )
    entries.sort(key=lambda item: -(item.get("lo") or 0.0))
    return entries


def _write_geojsonl_layer(
    features: list[dict[str, Any]],
    dest: Path,
    *,
    minzoom: int = 0,
) -> int:
    if not features:
        return 0
    with dest.open("w", encoding="utf-8") as handle:
        for feat in features:
            row = {**feat, "tippecanoe": {"minzoom": minzoom}} if minzoom > 0 else feat
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return len(features)


def _field_tile_features(
    fields_gdf: gpd.GeoDataFrame,
    field_stats: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    poly_feats: list[dict[str, Any]] = []
    centroid_feats: list[dict[str, Any]] = []
    for _, row in fields_gdf.iterrows():
        geom = row.geometry
        if not _has_valid_geom(geom):
            continue
        key = (str(row["source"]), str(row["field_id"]))
        st = field_stats.get(key, {})
        props = _compact_entry(
            {
                "source": key[0],
                "field_id": key[1],
                "field_name": _str_opt(row.get("field_name")) or key[1],
                "basin": _str_opt(row.get("basin")),
                "operator": _str_opt(row.get("operator")),
                "environment": _str_opt(row.get("environment")),
                "status": _str_opt(row.get("status")),
                "first_year": _int_opt(st.get("first_year")),
                "last_year": _int_opt(st.get("last_year")),
                "cum_oil": _round_opt(st.get("cum_oil_mmbbl"), 3),
                "latest_oil_bpd": _round_opt(st.get("latest_oil_bpd"), 1),
                "latest_gas_e3m3d": _round_opt(st.get("latest_gas_e3m3d"), 1),
            }
        )
        poly_feats.append(
            {"type": "Feature", "geometry": shapely.geometry.mapping(geom), "properties": props}
        )
        pt = geom.representative_point()
        centroid_feats.append(
            {"type": "Feature", "geometry": shapely.geometry.mapping(pt), "properties": props}
        )
    return poly_feats, centroid_feats


def _facility_tile_features(
    fac_gdf: gpd.GeoDataFrame,
    fac_stats: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    for _, row in fac_gdf.iterrows():
        geom = row.geometry
        if not _has_valid_geom(geom):
            continue
        key = (str(row["source"]), str(row["facility_id"]))
        st = fac_stats.get(key, {})
        oil_cap = _round_opt(row.get("oil_capacity_bpd"), 1)
        latest_oil = _round_opt(st.get("latest_oil_bpd"), 1)
        util = (
            round(latest_oil / oil_cap, 4)
            if oil_cap and oil_cap > 0 and latest_oil is not None
            else None
        )
        props = _compact_entry(
            {
                "source": key[0],
                "facility_id": key[1],
                "facility_name": _str_opt(row.get("facility_name")) or key[1],
                "facility_type": _str_opt(row.get("facility_type")),
                "operator": _str_opt(row.get("operator")),
                "fields": _str_opt(row.get("fields")),
                "water_depth_m": _round_opt(row.get("water_depth_m"), 1),
                "oil_capacity_bpd": oil_cap,
                "gas_capacity_e3m3d": _round_opt(row.get("gas_capacity_e3m3d"), 1),
                "first_year": _int_opt(st.get("first_year")),
                "last_year": _int_opt(st.get("last_year")),
                "latest_oil_bpd": latest_oil,
                "latest_gas_e3m3d": _round_opt(st.get("latest_gas_e3m3d"), 1),
                "oil_utilization": util,
            }
        )
        feats.append(
            {
                "type": "Feature",
                "geometry": shapely.geometry.mapping(geom),
                "properties": props,
            }
        )
    return feats


def _well_tile_features(
    wells_gdf: gpd.GeoDataFrame,
    well_stats: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    feats: list[dict[str, Any]] = []
    for _, row in wells_gdf.iterrows():
        geom = row.geometry
        if not _has_valid_geom(geom):
            continue
        key = (str(row["source"]), str(row["well_id"]))
        st = well_stats.get(key, {})
        props = _compact_entry(
            {
                "source": key[0],
                "well_id": key[1],
                "well_name": _str_opt(row.get("well_name")) or key[1],
                "field_id": _str_opt(row.get("field_id")),
                "field_name": _str_opt(row.get("field_name")),
                "basin": _str_opt(row.get("basin")),
                "environment": _str_opt(row.get("environment")),
                "status": _str_opt(row.get("status")),
                "lift_method": _str_opt(row.get("lift_method")),
                "first_year": _int_opt(st.get("first_year")),
                "last_year": _int_opt(st.get("last_year")),
                "cum_oil": _round_opt(st.get("cum_oil_mmbbl"), 3),
                "latest_oil_bpd": _round_opt(st.get("latest_oil_bpd"), 1),
            }
        )
        feats.append(
            {
                "type": "Feature",
                "geometry": shapely.geometry.mapping(geom),
                "properties": props,
            }
        )
    return feats


def _build_pmtiles(
    out_path: Path,
    *,
    fields_gdf: gpd.GeoDataFrame,
    fac_gdf: gpd.GeoDataFrame,
    wells_gdf: gpd.GeoDataFrame,
    stats: dict[str, dict[tuple[str, str], dict[str, Any]]],
    tippecanoe_bin: str,
) -> bool:
    poly_feats, centroid_feats = _field_tile_features(fields_gdf, stats["fields"])
    fac_feats = _facility_tile_features(fac_gdf, stats["facilities"])
    well_feats = _well_tile_features(wells_gdf, stats["wells"])

    with tempfile.TemporaryDirectory(prefix="ogprod-tiles-") as tmp:
        tmp_dir = Path(tmp)
        layer_specs = [
            ("fields", poly_feats, tmp_dir / "fields.geojsonl"),
            ("field_centroids", centroid_feats, tmp_dir / "field_centroids.geojsonl"),
            ("facilities", fac_feats, tmp_dir / "facilities.geojsonl"),
            ("wells", well_feats, tmp_dir / "wells.geojsonl"),
        ]
        layer_args: list[str] = []
        for layer_name, feats, path in layer_specs:
            minzoom = _TILE_LAYER_MIN_ZOOM[layer_name]
            if _write_geojsonl_layer(feats, path, minzoom=minzoom) > 0:
                layer_args.extend(["-L", f"{layer_name}:{path}"])
        if not layer_args:
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_pmtiles = out_path.with_name(f"{out_path.name}.tmp.pmtiles")
        # --drop-rate=1 disables tippecanoe's default 2.5x/zoom point thinning below
        # maxzoom (which otherwise drops >99% of facilities and field labels at z5-z6);
        # --drop-densest-as-needed still enforces per-tile size limits where dense.
        cmd = [
            tippecanoe_bin,
            "-o",
            str(tmp_pmtiles),
            "--force",
            f"--minimum-zoom={_TILE_MIN_ZOOM}",
            f"--maximum-zoom={_TILE_MAX_ZOOM}",
            "--drop-rate=1",
            "--drop-densest-as-needed",
            "--extend-zooms-if-still-dropping",
            *layer_args,
        ]
        subprocess.run(cmd, check=True)  # noqa: S603
        tmp_pmtiles.replace(out_path)
    return True


def _layer_bounds(
    gdf: gpd.GeoDataFrame, source_name: str
) -> tuple[float, float, float, float] | None:
    if len(gdf) == 0:
        return None
    sub = gdf.loc[gdf["source"] == source_name]
    non_null = sub.geometry.dropna()
    non_null = non_null.loc[~non_null.is_empty]
    if len(non_null) == 0:
        return None
    b = non_null.total_bounds
    return (float(b[0]), float(b[1]), float(b[2]), float(b[3]))


def _source_bbox(
    source_name: str,
    geo_frames: tuple[gpd.GeoDataFrame, ...],
) -> list[float] | None:
    if geo_frames:
        field_bounds = _layer_bounds(geo_frames[0], source_name)
        if field_bounds is not None:
            return [round(v, 4) for v in field_bounds]
    bounds = [b for gdf in geo_frames if (b := _layer_bounds(gdf, source_name)) is not None]
    if not bounds:
        return None
    return [
        round(min(b[0] for b in bounds), 4),
        round(min(b[1] for b in bounds), 4),
        round(max(b[2] for b in bounds), 4),
        round(max(b[3] for b in bounds), 4),
    ]


def _source_years_and_products(
    con: duckdb.DuckDBPyConnection, source_name: str
) -> tuple[list[int], list[str]]:
    years_df = con.execute(
        "SELECT DISTINCT year(period) AS y FROM production WHERE source = ? ORDER BY 1",
        [source_name],
    ).df()
    years = [int(y) for y in years_df["y"].tolist()]
    counts_row = con.execute(_PRODUCT_COUNTS_SQL, [source_name]).fetchone()
    products = [
        col
        for idx, col in enumerate(schema.RATE_COLUMNS)
        if counts_row is not None and int(counts_row[idx]) > 0
    ]
    return years, products


def _build_meta(
    db: store.Store,
    con: duckdb.DuckDBPyConnection,
    *,
    country_df: pd.DataFrame,
    geo_frames: tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, gpd.GeoDataFrame],
    mirror_repo: str,
    revision: str,
    has_tiles: bool,
) -> dict[str, Any]:
    cov = db.coverage()
    local_manifest = db.manifest().get("sources", {})
    sources_list: list[dict[str, Any]] = []

    for rec in cov.to_dict("records"):
        src = str(rec["source"])
        conn = sources.get(src) if src in sources.available() else None
        years, products = _source_years_and_products(con, src)
        src_country = country_df.loc[country_df["source"] == src]
        latest_oil = _round_opt(src_country["oil_prod"].iloc[-1], 1) if len(src_country) else None
        latest_gas = _round_opt(src_country["gas_prod"].iloc[-1], 1) if len(src_country) else None
        first_p = rec.get("first_period")
        last_p = rec.get("last_period")
        sources_list.append(
            {
                "id": src,
                "country": conn.country if conn else src.title(),
                "country_code": conn.country_code if conn else src[:2].upper(),
                "publisher": conn.publisher if conn else src,
                "docs_url": conn.docs_url if conn else "",
                "licence": _LICENCES.get(src, "Public open data"),
                "entity_type": conn.entity_type.value if conn else "field",
                "bbox": _source_bbox(src, geo_frames),
                "years": years,
                "first_period": first_p.strftime("%Y-%m-%d")
                if hasattr(first_p, "strftime")
                else None,
                "last_period": last_p.strftime("%Y-%m-%d") if hasattr(last_p, "strftime") else None,
                "updated_at": local_manifest.get(src, {}).get("updated_at"),
                "counts": {
                    "production_rows": int(rec["rows"]),
                    "producing_entities": int(rec["producing_entities"]),
                    "fields": int(rec["fields"]),
                    "facilities": int(rec["facilities"]),
                    "wells": int(rec["wells"]),
                },
                "latest_oil_bpd": latest_oil,
                "latest_gas_e3m3d": latest_gas,
                "available_products": products,
                "known_gaps": _known_gaps_for_source(src),
            }
        )

    return {
        "schema_version": 2,
        "generated_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mirror": {
            "repo": mirror_repo,
            "revision": revision,
            "base_url": mirror.mirror_base_url(mirror_repo, revision=revision),
        },
        "units": dict(schema.RATE_UNITS),
        "has_tiles": has_tiles,
        "sources": sources_list,
    }


def sync_all_from_mirror(
    db: store.Store,
    *,
    mirror_repo: str | None = None,
    revision: str = "main",
    client: Any | None = None,
) -> list[str]:
    """Fetch every source listed in the mirror manifest into ``db``."""
    resolved = mirror.resolve_mirror(mirror_repo)
    from ogprod.sources import base as src_base

    with src_base.open_client(client) as http:
        remote_manifest = mirror.fetch_manifest(http, mirror=resolved, revision=revision)
        synced: list[str] = []
        for src_name in sorted(remote_manifest.get("sources", {})):
            if src_name not in sources.available():
                continue
            db.ingest(
                src_name,
                origin="mirror",
                mirror=resolved,
                revision=revision,
                client=http,
            )
            synced.append(src_name)
    return synced


def export_web(
    *,
    data_dir: str | Path | None = None,
    out_dir: str | Path = Path("data/web"),
    sync_mirror: bool = False,
    mirror_repo: str | None = None,
    revision: str = "main",
    skip_tiles: bool = False,
    client: Any | None = None,
) -> ExportSummary:
    """Export Layer-2 web visualization artifacts (meta, rollups, search, PMTiles)."""
    db = store.Store(data_dir)
    resolved_mirror = mirror.resolve_mirror(mirror_repo)
    if sync_mirror:
        sync_all_from_mirror(db, mirror_repo=resolved_mirror, revision=revision, client=client)

    target_dir = Path(out_dir)
    rollups_dir = target_dir / "rollups"
    search_dir = target_dir / "search"
    rollups_dir.mkdir(parents=True, exist_ok=True)
    search_dir.mkdir(parents=True, exist_ok=True)

    fields_gdf = db.fields()
    fac_gdf = db.facilities()
    wells_gdf = db.wells()

    with duckdb.connect() as con:
        store._register_store_views(con, db.root)
        country_df, _ = _export_country_and_basin_rollups(con, rollups_dir)
        _export_entity_monthly_rollups(con, rollups_dir)
        stats = _load_entity_stats(con)

        fields_summary = _build_fields_summary(fields_gdf, stats["fields"])
        fac_summary = _build_facilities_summary(fac_gdf, stats["facilities"])
        _atomic_write_json(rollups_dir / "fields_summary.json", fields_summary)
        _atomic_write_json(rollups_dir / "facilities_summary.json", fac_summary)

        search_entries = _build_search_index(fields_summary, fac_summary, wells_gdf, stats["wells"])
        _atomic_write_json(search_dir / "index.json", search_entries)

        has_tiles = False
        if not skip_tiles:
            tippecanoe_bin = shutil.which("tippecanoe")
            if tippecanoe_bin is None:
                raise RuntimeError(
                    "tippecanoe not found on PATH; install Felt's tippecanoe or pass --skip-tiles"
                )
            has_tiles = _build_pmtiles(
                target_dir / "tiles" / "ogprod.pmtiles",
                fields_gdf=fields_gdf,
                fac_gdf=fac_gdf,
                wells_gdf=wells_gdf,
                stats=stats,
                tippecanoe_bin=tippecanoe_bin,
            )

        meta = _build_meta(
            db,
            con,
            country_df=country_df,
            geo_frames=(fields_gdf, fac_gdf, wells_gdf),
            mirror_repo=resolved_mirror,
            revision=revision,
            has_tiles=has_tiles,
        )
        _atomic_write_json(target_dir / "meta.json", meta)

    exported_sources = tuple(s["id"] for s in meta["sources"])
    return ExportSummary(
        out_dir=target_dir,
        sources=exported_sources,
        search_entries=len(search_entries),
        has_tiles=has_tiles,
    )
