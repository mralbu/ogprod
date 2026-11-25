"""Colombia — ANH (Agencia Nacional de Hidrocarburos / datos.gov.co) connector.

ANH publishes consolidated monthly field-level fiscalized production on Colombia's
national open-data portal (`datos.gov.co`, Socrata Open Data API) under CC BY-SA 4.0,
covering January 2014 to present:

- Crude oil (`fdvb-hsrf`): monthly total barrels (``produccion_bls``) -> ``bbl/d``.
- Natural gas (`5dux-bfvx`): monthly total thousand cubic feet (``produccionkpc``,
  Mcf) -> ``e3m3/d`` via exact ISO ``0.028316846592 e3m3/Mcf``.
- Fields (`geo/fields`): official ANH RUTY reservoir ring-fence polygons from the
  public ``Yacimientos:Yacimientos`` OGC WFS dissolved to field grain (with
  ``area_km2`` computed in ``EPSG:9377``), falling back to Socrata field ``Point``
  coordinates (with ``(0, 0)`` mapped to ``NULL``) for fields not yet in RUTY,
  all enriched with sedimentary basin polygons and onshore/offshore classification
  from ANH Geovisor's ``Cuenca Sedimentaria`` layer
  (``GEOVISOR_v32/ANH_InsGDB/MapServer/4``).

Production is field-level (``entity_type = "field"``, ``web_wells = False`` per
ADR 0011). Rows spanning multiple municipalities for the same field and month
(including ``MunicipiosNN`` unallocated rows) are summed to a single canonical
``(field_id, period)`` row.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa
import shapely

from ogprod import geo, schema, units
from ogprod.sources import base

_DOCS_URL = (
    "https://www.datos.gov.co/Minas-y-Energ-a/Produccion-Fiscalizada-Crudo-Consolidada/fdvb-hsrf"
)
_LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
_SOCRATA_BASE = "https://www.datos.gov.co/resource"
_OIL_DATASET_ID = "fdvb-hsrf"
_GAS_DATASET_ID = "5dux-bfvx"
_BASINS_QUERY_URL = (
    "https://geovisor.anh.gov.co/server/rest/services/GEOVISOR_v32/ANH_InsGDB/MapServer/4/query"
)
_YACIMIENTOS_WFS_URL = (
    "https://geovisor.anh.gov.co/server/services/Yacimientos/Yacimientos/MapServer/WFSServer"
)
_COLOMBIA_METRIC_CRS = "EPSG:9377"

FIRST_YEAR = 2014
_HISTORY_CHUNK_YEARS = 5
_SOCRATA_PAGE_SIZE = 50_000
_MCF_TO_E3M3 = units.volume_factor("mcf", "e3m3")

# Bounding box covering mainland Colombia plus Caribbean/Pacific offshore basins.
_COLOMBIA_LON_MIN = -85.0
_COLOMBIA_LON_MAX = -65.0
_COLOMBIA_LAT_MIN = -5.0
_COLOMBIA_LAT_MAX = 16.0

_DEPT_LABELS: dict[str, str] = {
    "AMAZONAS": "Amazonas",
    "ANTIOQUIA": "Antioquia",
    "ARAUCA": "Arauca",
    "ATLANTICO": "Atlántico",
    "BOLIVAR": "Bolívar",
    "BOYACA": "Boyacá",
    "CALDAS": "Caldas",
    "CAQUETA": "Caquetá",
    "CASANARE": "Casanare",
    "CAUCA": "Cauca",
    "CESAR": "Cesar",
    "CHOCO": "Chocó",
    "CORDOBA": "Córdoba",
    "CUNDINAMARCA": "Cundinamarca",
    "GUAJIRA": "Guajira",
    "HUILA": "Huila",
    "MAGDALENA": "Magdalena",
    "META": "Meta",
    "NARINO": "Nariño",
    "NORTE DE SANTANDER": "Norte de Santander",
    "PUTUMAYO": "Putumayo",
    "SANTANDER": "Santander",
    "SUCRE": "Sucre",
    "TOLIMA": "Tolima",
    "VALLE DEL CAUCA": "Valle del Cauca",
    "VICHADA": "Vichada",
}
_IGNORED_DEPTS = frozenset({"", "DEPARTAMENTO NN", "NN", "SIN INFORMACION"})

_BASIN_LABELS: dict[str, str] = {
    "AMAGA": "Amagá",
    "CAGUAN-PUTUMAYO": "Caguán-Putumayo",
    "CATATUMBO": "Catatumbo",
    "CAUCA PATIA": "Cauca-Patía",
    "CESAR RANCHERIA": "Cesar-Ranchería",
    "CHOCO": "Chocó",
    "CHOCO OFFSHORE": "Chocó Offshore",
    "COLOMBIA": "Colombia Offshore",
    "CORDILLERA ORIENTAL": "Cordillera Oriental",
    "GUAJIRA": "Guajira",
    "GUAJIRA OFFSHORE": "Guajira Offshore",
    "LLANOS ORIENTALES": "Llanos Orientales",
    "LOS CAYOS": "Los Cayos",
    "PACIFICO PROFUNDO COLOMBIANO": "Pacífico Profundo Colombiano",
    "SINU OFFSHORE": "Sinú Offshore",
    "SINU-SAN JACINTO": "Sinú-San Jacinto",
    "TUMACO": "Tumaco",
    "TUMACO OFFSHORE": "Tumaco Offshore",
    "URABA": "Urabá",
    "VALLE INFERIOR DEL MAGDALENA": "Valle Inferior del Magdalena",
    "VALLE MEDIO DEL MAGDALENA": "Valle Medio del Magdalena",
    "VALLE SUPERIOR DEL MAGDALENA": "Valle Superior del Magdalena",
    "VAUPES-AMAZONAS": "Vaupés-Amazonas",
}
_IGNORED_BASINS = frozenset({"", "AREA NO PROSPECTIVA"})

_LOWERCASE_WORDS = frozenset({"de", "del", "la", "las", "los", "y"})


def _title_case_es(text: str) -> str:
    words = text.strip().split()
    out: list[str] = []
    for idx, word in enumerate(words):
        lower = word.lower()
        out.append(lower if idx > 0 and lower in _LOWERCASE_WORDS else lower.capitalize())
    return " ".join(out)


def _clean_department(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    norm = schema.normalize_key(str(value))
    if norm in _IGNORED_DEPTS:
        return None
    return _DEPT_LABELS.get(norm) or _title_case_es(str(value))


def _clean_basin(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    norm = schema.normalize_key(str(value))
    if norm in _IGNORED_BASINS:
        return None
    return _BASIN_LABELS.get(norm) or _title_case_es(str(value))


def _clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _parse_numeric_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype("string").str.replace(",", "", regex=False).str.strip()
    return pd.to_numeric(cleaned, errors="coerce")


def _year_ranges(
    *,
    year: int | None,
    recent: bool,
    current_year: int,
) -> list[tuple[int, int]]:
    if year is not None:
        return [(year, year)]
    if recent:
        start = max(FIRST_YEAR, current_year - 1)
        return [(y, y) for y in range(start, current_year + 1)]
    ranges: list[tuple[int, int]] = []
    hist_end = current_year - 2
    y = FIRST_YEAR
    while y <= hist_end:
        chunk_end = min(y + _HISTORY_CHUNK_YEARS - 1, hist_end)
        ranges.append((y, chunk_end))
        y = chunk_end + 1
    for recent_yr in range(max(FIRST_YEAR, current_year - 1), current_year + 1):
        ranges.append((recent_yr, recent_yr))
    return ranges


def _remote_for_range(y0: int, y1: int) -> base.RemoteFile:
    query = urlencode({"yearStart": y0, "yearEnd": y1})
    span = f"{y0}" if y0 == y1 else f"{y0}_{y1}"
    return base.RemoteFile(
        url=f"{_SOCRATA_BASE}/{_OIL_DATASET_ID}.json?{query}",
        key=f"colombia_{span}.json",
        year_min=y0,
        year_max=y1,
    )


def _parse_remote_years(url: str) -> tuple[int, int]:
    qs = parse_qs(urlparse(url).query)
    try:
        y0 = int(qs["yearStart"][0])
        y1 = int(qs["yearEnd"][0])
    except (KeyError, IndexError, ValueError) as err:
        raise ValueError(f"invalid Colombia production query URL: {url!r}") from err
    return y0, y1


def _fetch_socrata_rows(
    client: httpx2.Client,
    dataset_id: str,
    *,
    y0: int,
    y1: int,
    select: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    where = f"vigencia>={y0} AND vigencia<={y1}"
    while True:
        params = urlencode(
            {
                "$select": select,
                "$where": where,
                "$order": ":id",
                "$limit": _SOCRATA_PAGE_SIZE,
                "$offset": offset,
            }
        )
        url = f"{_SOCRATA_BASE}/{dataset_id}.json?{params}"
        resp = base.get_with_retry(client, url)
        page = resp.json()
        if not isinstance(page, list):
            raise ValueError(
                f"expected JSON list from datos.gov.co/{dataset_id}, got {type(page).__name__}"
            )
        rows.extend(page)
        if len(page) < _SOCRATA_PAGE_SIZE:
            break
        offset += _SOCRATA_PAGE_SIZE
    return rows


def _read_raw_text(raw: base.RawFile) -> str:
    if isinstance(raw, (str, Path)):
        return Path(raw).read_text(encoding="utf-8-sig")
    data = raw.read()
    if isinstance(data, bytes):
        return data.decode("utf-8-sig")
    return str(data)


def _load_raw_frames(raw: base.RawFile) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Decode either our combined JSON bundle or a standalone Socrata oil/gas CSV."""
    text = _read_raw_text(raw).lstrip()
    if not text:
        return pd.DataFrame(), pd.DataFrame()
    if text.startswith("{"):
        bundle = json.loads(text)
        if not isinstance(bundle, dict) or ("oil" not in bundle and "gas" not in bundle):
            raise ValueError("invalid Colombia production bundle: expected 'oil'/'gas' keys")
        return pd.DataFrame(bundle.get("oil") or []), pd.DataFrame(bundle.get("gas") or [])
    df = pd.read_csv(io.StringIO(text), dtype=str)
    cols = {c.strip().lower() for c in df.columns}
    if "produccion_bls" in cols:
        return df, pd.DataFrame()
    if "produccionkpc" in cols:
        return pd.DataFrame(), df
    raise ValueError(
        "unrecognized Colombia production CSV: expected 'produccion_bls' or 'produccionkpc'"
    )


def _aggregate_stream(df: pd.DataFrame, *, value_col: str, out_col: str) -> pd.DataFrame:
    """Normalize and sum a Socrata oil or gas table to one row per ``(field_id, period)``."""
    empty = pd.DataFrame(
        columns=["field_id", "period", out_col, "field_name", "operator", "region"]
    )
    if df.empty:
        return empty
    work = df.copy()
    work.columns = [str(c).strip().lower() for c in work.columns]
    if "campo" not in work.columns or value_col not in work.columns:
        return empty

    campo = work["campo"].astype("string").str.strip()
    year = pd.to_numeric(work.get("vigencia"), errors="coerce")
    month = pd.to_numeric(work.get("mes"), errors="coerce")
    valid = campo.notna() & (campo != "") & year.notna() & month.between(1, 12)
    if not valid.any():
        return empty

    sub = work.loc[valid].copy()
    sub["field_name"] = campo.loc[valid]
    sub["field_id"] = sub["field_name"].map(schema.normalize_key)
    sub = sub.loc[sub["field_id"] != ""]
    if sub.empty:
        return empty

    y_int = year.loc[sub.index].astype(int)
    m_int = month.loc[sub.index].astype(int)
    sub["period"] = pd.to_datetime(pd.DataFrame({"year": y_int, "month": m_int, "day": 1}))
    sub[out_col] = _parse_numeric_series(sub[value_col])
    sub["operator"] = sub["operadora"].map(_clean_text) if "operadora" in sub.columns else None
    sub["region"] = (
        sub["departamento"].map(_clean_department) if "departamento" in sub.columns else None
    )

    # Sort descending by volume so metadata (field_name, operator, region) comes from
    # the primary producing municipality when a field spans multiple municipalities.
    sub = sub.sort_values(
        ["field_id", "period", out_col],
        ascending=[True, True, False],
        na_position="last",
        kind="mergesort",
    )
    grouped = sub.groupby(["field_id", "period"], as_index=False, sort=False).agg(
        {
            out_col: lambda s: s.sum(min_count=1),
            "field_name": "first",
            "operator": "first",
            "region": "first",
        }
    )
    return grouped


def _merge_oil_and_gas(oil_agg: pd.DataFrame, gas_agg: pd.DataFrame) -> pd.DataFrame:
    if oil_agg.empty and gas_agg.empty:
        return pd.DataFrame()
    if oil_agg.empty:
        merged = gas_agg.copy()
        merged["oil_bbl"] = np.nan
        return merged
    if gas_agg.empty:
        merged = oil_agg.copy()
        merged["gas_mcf"] = np.nan
        return merged

    merged = oil_agg.merge(
        gas_agg,
        on=["field_id", "period"],
        how="outer",
        suffixes=("_oil", "_gas"),
    )
    for col in ("field_name", "operator", "region"):
        merged[col] = merged[f"{col}_oil"].combine_first(merged[f"{col}_gas"])
    return merged[["field_id", "period", "oil_bbl", "gas_mcf", "field_name", "operator", "region"]]


def _parse_production_frames(oil_df: pd.DataFrame, gas_df: pd.DataFrame) -> pa.Table:
    oil_agg = _aggregate_stream(oil_df, value_col="produccion_bls", out_col="oil_bbl")
    gas_agg = _aggregate_stream(gas_df, value_col="produccionkpc", out_col="gas_mcf")
    merged = _merge_oil_and_gas(oil_agg, gas_agg)
    if merged.empty:
        return schema.empty_production()

    # Drop rows where neither oil nor gas has a numeric value.
    has_rate = merged["oil_bbl"].notna() | merged["gas_mcf"].notna()
    merged = merged.loc[has_rate].reset_index(drop=True)
    if merged.empty:
        return schema.empty_production()

    days = merged["period"].dt.days_in_month.astype("float64")
    oil_bpd = merged["oil_bbl"].astype("float64") / days
    gas_e3m3d = (merged["gas_mcf"].astype("float64") * _MCF_TO_E3M3) / days

    return base.build_production_table(
        source="colombia",
        entity_type=schema.EntityType.FIELD,
        entity_id=merged["field_id"],
        entity_name=merged["field_name"],
        field_id=merged["field_id"],
        field_name=merged["field_name"],
        region=merged["region"],
        operator=merged["operator"],
        period=merged["period"],
        rates={"oil_prod": oil_bpd, "gas_prod": gas_e3m3d},
    )


def _valid_colombia_point(lon: float, lat: float) -> shapely.Point | None:
    if not (np.isfinite(lon) and np.isfinite(lat)):
        return None
    if lon == 0.0 and lat == 0.0:
        return None
    if not (_COLOMBIA_LON_MIN <= lon <= _COLOMBIA_LON_MAX):
        return None
    if not (_COLOMBIA_LAT_MIN <= lat <= _COLOMBIA_LAT_MAX):
        return None
    return shapely.Point(lon, lat)


def _read_field_summary_csv(path: Path, *, stream: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)
    if df.empty:
        return pd.DataFrame()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "campo" not in df.columns:
        return pd.DataFrame()
    campo = df["campo"].astype("string").str.strip()
    valid = campo.notna() & (campo != "")
    if not valid.any():
        return pd.DataFrame()

    sub = df.loc[valid].copy()
    sub["field_name"] = campo.loc[valid]
    sub["field_id"] = sub["field_name"].map(schema.normalize_key)
    sub = sub.loc[sub["field_id"] != ""]
    sub["region"] = (
        sub["departamento"].map(_clean_department) if "departamento" in sub.columns else None
    )
    sub["operator"] = sub["operadora"].map(_clean_text) if "operadora" in sub.columns else None
    sub["lon"] = pd.to_numeric(sub.get("longitud"), errors="coerce")
    sub["lat"] = pd.to_numeric(sub.get("latitud"), errors="coerce")
    year_col = "last_year" if "last_year" in sub.columns else "vigencia"
    sub["last_year"] = (
        pd.to_numeric(sub[year_col], errors="coerce").fillna(0).astype(int)
        if year_col in sub.columns
        else 0
    )
    sub["stream"] = stream
    return sub[
        ["field_id", "field_name", "region", "operator", "lon", "lat", "last_year", "stream"]
    ]


def _basin_environment(tipo: Any) -> str | None:
    if tipo is None or pd.isna(tipo):
        return None
    return "offshore" if str(tipo).strip().upper() == "OFFSHORE" else "onshore"


def _load_basins_frame(basins_path: Path | None) -> gpd.GeoDataFrame | None:
    if basins_path is None or not basins_path.exists():
        return None
    basins = gpd.read_file(basins_path)
    if basins.empty:
        return None
    if basins.crs is None:
        return basins.set_crs(schema.GEO_CRS)
    if basins.crs.to_epsg() != 4326:
        return basins.to_crs(schema.GEO_CRS)
    return basins


def _enrich_fields_with_basins(
    fields_gdf: gpd.GeoDataFrame,
    basins_path: Path | None,
) -> gpd.GeoDataFrame:
    out = fields_gdf.copy()
    out["basin"] = pd.NA
    out["environment"] = pd.NA
    basins = _load_basins_frame(basins_path)
    if basins is None or out.empty:
        return out

    has_geom = out.geometry.notna() & ~out.geometry.is_empty
    name_col = next((c for c in basins.columns if c.upper() == "NOMBRE"), None)
    if not has_geom.any() or name_col is None:
        return out

    tipo_col = next((c for c in basins.columns if c.upper() == "TIPO"), None)
    cols = [name_col, "geometry"] + ([tipo_col] if tipo_col else [])
    rep_pts = out.geometry.loc[has_geom].apply(
        lambda g: g if g.geom_type == "Point" else g.representative_point()
    )
    pts = gpd.GeoDataFrame(
        {"field_id": out.loc[has_geom, "field_id"]},
        geometry=rep_pts,
        crs=schema.GEO_CRS,
    )
    joined = gpd.sjoin(pts, basins[cols], how="left", predicate="intersects")
    joined = joined.drop_duplicates(subset=["field_id"], keep="first")

    basin_map = {
        fid: _clean_basin(val)
        for fid, val in zip(joined["field_id"], joined[name_col], strict=True)
    }
    out["basin"] = out["field_id"].map(basin_map)

    if tipo_col is not None:
        env_map = {
            fid: _basin_environment(val)
            for fid, val in zip(joined["field_id"], joined[tipo_col], strict=True)
        }
        out["environment"] = out["field_id"].map(env_map)
    return out


def _primary_fluid(streams: set[str]) -> str | None:
    if streams == {"oil", "gas"}:
        return "oil_and_gas"
    if "oil" in streams:
        return "oil"
    if "gas" in streams:
        return "gas"
    return None


def _map_ruty_fluid(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    norm = schema.normalize_key(str(value))
    has_oil = "PETROLEO" in norm or "CRUDO" in norm or "CONDENSADO" in norm
    has_gas = "GAS" in norm
    if has_oil and has_gas:
        return "oil_and_gas"
    if has_oil:
        return "oil"
    if has_gas:
        return "gas"
    return None


def _clean_ruty_status(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None or text.lower() in {"null", "sin dato", "no aplica"}:
        return None
    return _title_case_es(text)


def _extract_polygonal(geom: Any) -> Any:
    if geom is None or geom.is_empty:
        return None
    valid = geom if geom.is_valid else shapely.make_valid(geom)
    if valid.geom_type in {"Polygon", "MultiPolygon"}:
        return valid
    if valid.geom_type == "GeometryCollection":
        polys = [g for g in valid.geoms if g.geom_type in {"Polygon", "MultiPolygon"}]
        if not polys:
            return None
        return shapely.unary_union(polys)
    return None


def _parse_yacimientos_polygons(path: Path | None) -> gpd.GeoDataFrame | None:
    """Load and dissolve ANH RUTY reservoir polygons into one ring fence per ``field_id``."""
    if path is None or not path.exists():
        return None
    raw = gpd.read_file(path)
    if raw.empty:
        return None
    if raw.crs is None:
        raw = raw.set_crs(schema.GEO_CRS)
    elif raw.crs.to_epsg() != 4326:
        raw = raw.to_crs(schema.GEO_CRS)

    campo_col = next((c for c in raw.columns if c.upper() == "CAMPO"), None)
    if campo_col is None:
        return None

    work = raw.copy()
    work["field_name"] = work[campo_col].astype("string").str.strip()
    work["field_id"] = work["field_name"].map(schema.normalize_key)
    work["geometry"] = work.geometry.map(_extract_polygonal)
    work = work.loc[(work["field_id"] != "") & work.geometry.notna()]
    if work.empty:
        return None

    code_col = next((c for c in work.columns if c.upper() == "CAMPO_ID"), None)
    status_col = next((c for c in work.columns if c.upper() == "ESTADO_YACIMIENTO"), None)
    fluid_col = next(
        (c for c in work.columns if c.upper() in {"TIPO_HIDROCARBURO", "TIPO_HIDRO"}), None
    )

    work["field_code"] = work[code_col].map(_clean_text) if code_col else None
    work["status"] = work[status_col].map(_clean_ruty_status) if status_col else None
    work["primary_fluid"] = work[fluid_col].map(_map_ruty_fluid) if fluid_col else None

    dissolved = work[
        ["field_id", "field_name", "field_code", "status", "primary_fluid", "geometry"]
    ].dissolve(
        by="field_id",
        aggfunc={
            "field_name": "first",
            "field_code": "first",
            "status": "first",
            "primary_fluid": "first",
        },
        as_index=False,
    )
    dissolved["geometry"] = dissolved.geometry.map(_extract_polygonal)
    dissolved["area_km2"] = (dissolved.to_crs(_COLOMBIA_METRIC_CRS).geometry.area / 1e6).astype(
        "float32"
    )
    return dissolved


def _build_socrata_points(frames: list[pd.DataFrame]) -> gpd.GeoDataFrame:
    non_empty = [f for f in frames if not f.empty]
    if not non_empty:
        return gpd.GeoDataFrame(
            columns=["field_id", "field_name", "region", "operator", "primary_fluid", "geometry"],
            geometry="geometry",
            crs=schema.GEO_CRS,
        )
    combined = pd.concat(non_empty, ignore_index=True)
    combined["has_coord"] = [
        _valid_colombia_point(lon, lat) is not None
        for lon, lat in zip(combined["lon"], combined["lat"], strict=True)
    ]
    # Prefer rows with valid coordinates, then most recent year, then oil over gas.
    combined["stream_rank"] = (combined["stream"] != "oil").astype(int)
    combined = combined.sort_values(
        ["field_id", "has_coord", "last_year", "stream_rank"],
        ascending=[True, False, False, True],
        kind="mergesort",
    )
    fluids = combined.groupby("field_id")["stream"].agg(lambda s: _primary_fluid(set(s)))
    dedup = combined.groupby("field_id", as_index=False, sort=False).agg(
        {
            "field_name": "first",
            "region": "first",
            "operator": "first",
            "lon": "first",
            "lat": "first",
        }
    )
    geoms = [
        _valid_colombia_point(lon, lat) for lon, lat in zip(dedup["lon"], dedup["lat"], strict=True)
    ]
    return gpd.GeoDataFrame(
        {
            "field_id": dedup["field_id"],
            "field_name": dedup["field_name"],
            "region": dedup["region"],
            "operator": dedup["operator"],
            "primary_fluid": dedup["field_id"].map(fluids),
        },
        geometry=gpd.GeoSeries(geoms, crs=schema.GEO_CRS),
        crs=schema.GEO_CRS,
    )


def _merge_points_and_polygons(
    points_gdf: gpd.GeoDataFrame,
    polys_gdf: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    if polys_gdf is None or polys_gdf.empty:
        out = points_gdf.copy()
        out["source"] = "colombia"
        return out
    if points_gdf.empty:
        out = polys_gdf.copy()
        out["source"] = "colombia"
        return out

    pts = pd.DataFrame(points_gdf).rename(columns={"geometry": "pt_geom"})
    ply = pd.DataFrame(polys_gdf).rename(columns={"geometry": "poly_geom"})
    merged = pts.merge(ply, on="field_id", how="outer", suffixes=("_pt", "_ply"))
    geoms = [
        pg if pg is not None and not pd.isna(pg) else ptg
        for pg, ptg in zip(merged["poly_geom"], merged["pt_geom"], strict=True)
    ]
    return gpd.GeoDataFrame(
        {
            "source": "colombia",
            "field_id": merged["field_id"],
            "field_code": merged["field_code"],
            "field_name": merged["field_name_pt"].combine_first(merged["field_name_ply"]),
            "region": merged["region"],
            "operator": merged["operator"],
            "status": merged["status"],
            "primary_fluid": merged["primary_fluid_pt"].combine_first(merged["primary_fluid_ply"]),
            "area_km2": merged["area_km2"],
        },
        geometry=gpd.GeoSeries(geoms, crs=schema.GEO_CRS),
        crs=schema.GEO_CRS,
    )


def _parse_fields_geo(paths: list[Path]) -> gpd.GeoDataFrame:
    frames: list[pd.DataFrame] = []
    basins_path: Path | None = None
    yacimientos_path: Path | None = None
    for path in paths:
        lower = path.name.lower()
        if lower.endswith((".geojson", ".json")):
            if "yacimiento" in lower:
                yacimientos_path = path
            else:
                basins_path = path
        elif "gas" in lower:
            frames.append(_read_field_summary_csv(path, stream="gas"))
        else:
            frames.append(_read_field_summary_csv(path, stream="oil"))

    points_gdf = _build_socrata_points(frames)
    polys_gdf = _parse_yacimientos_polygons(yacimientos_path)
    if points_gdf.empty and (polys_gdf is None or polys_gdf.empty):
        return schema.empty_geo(schema.GeoKind.FIELDS)

    combined = _merge_points_and_polygons(points_gdf, polys_gdf)
    enriched = _enrich_fields_with_basins(combined, basins_path)
    return geo.prepare_geo(enriched, schema.GeoKind.FIELDS)


@base.register
class Colombia(base.Source):
    """Colombia Agencia Nacional de Hidrocarburos (ANH) via ``datos.gov.co``."""

    name = "colombia"
    country = "Colombia"
    country_code = "CO"
    publisher = "ANH"
    entity_type = schema.EntityType.FIELD
    docs_url = _DOCS_URL
    licence = "CC BY-SA 4.0"
    licence_url = _LICENCE_URL
    web_wells = False
    search_all_wells = False

    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[base.RemoteFile]:
        """Return year-chunked Socrata production bundle specs from 2014 to present."""
        del client
        if environment is not None and environment.strip().lower() not in {"onshore", "offshore"}:
            raise ValueError(
                f"colombia environment must be 'onshore' or 'offshore', got {environment!r}"
            )
        current_year = datetime.now(UTC).year
        ranges = _year_ranges(year=year, recent=recent, current_year=current_year)
        return [_remote_for_range(y0, y1) for y0, y1 in ranges]

    def fetch_production(
        self,
        remote: base.RemoteFile,
        dest: Path,
        *,
        client: httpx2.Client,
    ) -> base.DownloadResult:
        """Fetch Socrata oil and gas rows for ``remote``'s year span into one JSON bundle."""
        y0, y1 = _parse_remote_years(remote.url)
        oil_rows = _fetch_socrata_rows(
            client,
            _OIL_DATASET_ID,
            y0=y0,
            y1=y1,
            select="vigencia,mes,departamento,operadora,campo,produccion_bls",
        )
        gas_rows = _fetch_socrata_rows(
            client,
            _GAS_DATASET_ID,
            y0=y0,
            y1=y1,
            select="vigencia,mes,departamento,operadora,campo,produccionkpc",
        )
        bundle = {
            "source": self.name,
            "year_min": y0,
            "year_max": y1,
            "oil": oil_rows,
            "gas": gas_rows,
        }
        payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f"{dest.name}.part")
        part.write_bytes(payload)
        part.replace(dest)
        return base.DownloadResult(
            path=dest,
            url=remote.url,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        """Parse a Colombia JSON bundle or standalone Socrata oil/gas CSV."""
        oil_df, gas_df = _load_raw_frames(raw)
        return _parse_production_frames(oil_df, gas_df)

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Download ANH RUTY field polygons, Socrata field points, and sedimentary basins."""
        if kind is not schema.GeoKind.FIELDS:
            return super().fetch_geo(kind, dest_dir, client=client)

        group_params = urlencode(
            {
                "$select": (
                    "campo,departamento,operadora,longitud,latitud,max(vigencia) as last_year"
                ),
                "$group": "campo,departamento,operadora,longitud,latitud",
                "$limit": _SOCRATA_PAGE_SIZE,
            }
        )
        oil_url = f"{_SOCRATA_BASE}/{_OIL_DATASET_ID}.csv?{group_params}"
        gas_url = f"{_SOCRATA_BASE}/{_GAS_DATASET_ID}.csv?{group_params}"
        basins_params = urlencode(
            {
                "where": "1=1",
                "outFields": "NOMBRE,TIPO",
                "outSR": "4326",
                "maxAllowableOffset": "0.001",
                "f": "geojson",
            }
        )
        basins_url = f"{_BASINS_QUERY_URL}?{basins_params}"
        yac_params = urlencode(
            {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": "Yacimientos:Yacimientos",
                "outputFormat": "GEOJSON",
            }
        )
        yac_url = f"{_YACIMIENTOS_WFS_URL}?{yac_params}"

        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            return [
                base.download_file(http, oil_url, dest_dir / "colombia_fields_oil.csv"),
                base.download_file(http, gas_url, dest_dir / "colombia_fields_gas.csv"),
                base.download_file(http, basins_url, dest_dir / "colombia_basins.geojson"),
                base.download_file(http, yac_url, dest_dir / "colombia_yacimientos.geojson"),
            ]

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse Colombia field polygons/points enriched with ANH sedimentary basins."""
        if kind is schema.GeoKind.FIELDS:
            return _parse_fields_geo(paths)
        return super().parse_geo(kind, paths)
