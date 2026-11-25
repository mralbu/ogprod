"""United Kingdom - NSTA (North Sea Transition Authority) PPRS production and spatial layers.

NSTA publishes monthly field-level production and injection back to 1975 via the
Petroleum Production Reporting System (PPRS) hosted ArcGIS Feature Service, plus
offshore and onshore spatial registries on the NSTA Open Data Hub (WGS84):

- Production & injection (1975-present, offshore + onshore): oil (``m3`` -> ``bbl/d``),
  condensate (``m3`` -> ``bbl/d``), associated + dry gas (``ksm3`` -> ``e3m3/d``),
  produced water (``m3`` -> ``bbl/d``), injected water (``m3`` -> ``bbl/d``), and
  injected gas (``ksm3`` -> ``e3m3/d``).
- Fields: ``Offshore_hydrocarbon_fields_(WGS84)`` and ``Onshore_hydrocarbon_fields_(WGS84)``
  polygons, dissolved on normalized field key after stripping fluid-split suffixes.
- Facilities: ``UKCS offshore infrastructure surface points WGS84`` (platforms, FPSOs,
  terminals, export pipeline system, status, start year).
- Wells: ``UKCS_offshore_wellbore_top_holes_(WGS84)`` and onshore petroleum wellbore
  top holes (WGS84), linked to ``field_id`` with spud/completion dates.

Contains information provided by the North Sea Transition Authority and/or other
third parties (NSTA User Agreement).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa

from ogprod import geo, schema, units
from ogprod.sources import base

_DOCS_URL = "https://www.nstauthority.co.uk/data-and-insights/data/themes/production/"
_ARCGIS_BASE = "https://services-eu1.arcgis.com/OZMfUznmLTnWccBc/arcgis/rest/services"

_PPRS_SERVICE = "UKCS_hydrocarbon_field_production_reports_PPRS_points_(WGS84)"
_OFFSHORE_FIELDS_SERVICE = "Offshore_hydrocarbon_fields_(WGS84)"
_ONSHORE_FIELDS_SERVICE = "Onshore_hydrocarbon_fields_(WGS84)"
_FACILITIES_SERVICE = "UKCS%20offshore%20infrastructure%20surface%20points%20WGS84"
_OFFSHORE_WELLS_SERVICE = "UKCS_offshore_wellbore_top_holes_(WGS84)"
_ONSHORE_WELLS_SERVICE = "UKCS%20onshore%20petroleum%20wells%20top%20holes%20WGS84"

_PPRS_OUT_FIELDS = (
    "OBJECTID,FIELDNAME,FIELDAREA,LOCATION,ORGGRPNM,UNITNAME,UNITTYPCOD,"
    "PERIODYR,PERIODMNTH,OILPRODM3,GCONDVOL,AGASPROKSM,DGASPROKSM,"
    "WATPRODVOL,INJWATVOL,GASINJVOL"
)
_OFFSHORE_FIELDS_OUT = "OBJECTID,FIELDNAME,FIELDTYPE,STATUS,DISC_DATE,PROD_DATE,DEPTH_M,CURR_OPER"
_ONSHORE_FIELDS_OUT = "OBJECTID,FIELDNAME,FIELDTYPE"
_FACILITIES_OUT = "OBJECTID,FEATURE_ID,NAME,INF_TYPE,REP_GROUP,PIPE_SYS,STATUS,START_DATE,CURR_ROW"
_OFFSHORE_WELLS_OUT = (
    "OBJECTID,WELLREGNO,TARGETFLD,SUBAREAOP,TDOPERATOR,WELLOPSTAT,COMPLESTAT,SPUDDATE,COMPLEDATE"
)
_ONSHORE_WELLS_OUT = _OFFSHORE_WELLS_OUT + ",ONS_COUNTY"

_REQUIRED_PROD = ("fieldname", "periodyr", "periodmnth")
_PAGE_SIZE = 2000
_FACILITIES_DEFAULT_LAYER_ID = 1

_FIELD_AREAS: dict[str, str] = {
    "NNS": "Northern North Sea",
    "CNS": "Central North Sea",
    "SNS": "Southern North Sea",
    "WOS": "West of Shetland",
    "IS": "Irish Sea",
    "LAND": "Onshore",
}

_ENVIRONMENTS: dict[str, str] = {
    "offshore": "Offshore",
    "onshore": "Onshore",
}

# Reconcile legacy PPRS reporting names with NSTA field polygon names (keyed by normalize_key).
_FIELD_KEY_ALIASES: dict[str, str] = {
    "NORTHWEST HUTTON": "NORTH WEST HUTTON",
    "CAISTER [BUNTER]": "CAISTER BUNTER",
    "CAISTER [CARBONIFEROUS]": "CAISTER CARBONIFEROUS",
    "RAVENSPURN N[PT.OF RAVENSPURN]": "RAVENSPURN NORTH",
    "RAVENSPURN S[PT.OF RAVENSPURN]": "RAVENSPURN SOUTH",
    "VIKING A [PT OF VIKING GROUP]": "VIKING A",
    "VIKING B [PT OF VIKING GROUP]": "VIKING B",
    "CURLEW B + D": "CURLEW",
    "SATURN (ATLAS, HYPERION, RHEA)": "SATURN",
    "SATURN (ANNABEL)": "SATURN",
}

_PART_OF_RE = re.compile(r"\[\s*(?:part|pt\.?)\s*of\s+([^\]]+)\]", re.IGNORECASE)
_BRACKET_RE = re.compile(r"\s*\[[^\]]*\]")
_FLUID_SUFFIX_RE = re.compile(r"\s*\((?:oil|gas|cond)\)\s*$", re.IGNORECASE)
_YM_DATE_RE = re.compile(r"^(\d{4})(?:[/-](\d{1,2})(?:[/-](\d{1,2}))?)?$")

_SENTINELS = frozenset({"no data available", "unknown", "n/a", "none", ""})
_STATUS_FIXES: dict[str, str] = {"Decomissioned": "Decommissioned"}


def resolve_field_key(name: str | None) -> str:
    """Resolve an NSTA PPRS or GIS field name to its canonical uppercase join key.

    Strips operator/unit annotations (``[SHELL]``), resolves parent-field qualifiers
    (``CLAIR-PHASE 1 [Part of CLAIR]`` -> ``CLAIR``), and collapses fluid-split
    polygon suffixes (``MARNOCK-SKUA (oil)`` -> ``MARNOCK-SKUA``).
    """
    norm = schema.normalize_key(name)
    if not norm:
        return ""
    if norm in _FIELD_KEY_ALIASES:
        return _FIELD_KEY_ALIASES[norm]
    if match := _PART_OF_RE.search(norm):
        return schema.normalize_key(match.group(1))
    cleaned = _FLUID_SUFFIX_RE.sub("", _BRACKET_RE.sub("", norm))
    return schema.normalize_key(cleaned)


def _strip_fluid_suffix(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.str.replace(_FLUID_SUFFIX_RE, "", regex=True).str.strip()


def _clean_sentinel(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    is_sentinel = text.str.lower().isin(_SENTINELS)
    return text.mask(is_sentinel, pd.NA)


def _normalize_area(series: pd.Series) -> pd.Series:
    cleaned = _clean_sentinel(series)
    return cleaned.map(
        lambda v: _FIELD_AREAS.get(str(v).upper(), str(v)) if pd.notna(v) else pd.NA
    ).astype("string")


def _normalize_env(series: pd.Series) -> pd.Series:
    cleaned = _clean_sentinel(series)
    return cleaned.str.lower().astype("string")


def _parse_one_ym_date(value: Any) -> Any:
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    match = _YM_DATE_RE.match(text)
    if not match:
        return pd.NA
    yr = int(match.group(1))
    mo = int(match.group(2)) if match.group(2) else 1
    dy = int(match.group(3)) if match.group(3) else 1
    if not (1 <= mo <= 12 and 1 <= dy <= 31):
        return pd.NA
    return f"{yr:04d}-{mo:02d}-{dy:02d}"


def _parse_ym_date(series: pd.Series) -> pd.Series:
    """Normalize NSTA ``YYYY/MM``, ``YYYY``, or ``YYYY-MM-DD`` dates to ISO ``YYYY-MM-DD``."""
    return series.map(_parse_one_ym_date).astype("string")


def _epoch_ms_to_iso(series: pd.Series) -> pd.Series:
    """Convert ArcGIS epoch-millisecond timestamps (including pre-1970 negatives) to ISO dates."""
    num = pd.to_numeric(series, errors="coerce")
    return num.map(
        lambda v: (
            datetime.fromtimestamp(float(v) / 1000.0, tz=UTC).strftime("%Y-%m-%d")
            if pd.notna(v)
            else pd.NA
        )
    ).astype("string")


def _epoch_ms_to_year(series: pd.Series) -> pd.Series:
    num = pd.to_numeric(series, errors="coerce")
    return num.map(
        lambda v: datetime.fromtimestamp(float(v) / 1000.0, tz=UTC).year if pd.notna(v) else pd.NA
    ).astype("Int32")


def _has_more_arcgis_pages(payload: dict[str, Any], page_count: int) -> bool:
    if page_count == 0:
        return False
    exceeded = payload.get("exceededTransferLimit")
    if exceeded is None and isinstance(payload.get("properties"), dict):
        exceeded = payload["properties"].get("exceededTransferLimit")
    if isinstance(exceeded, bool):
        return exceeded
    return page_count >= _PAGE_SIZE


def _arcgis_query_url(
    service: str,
    layer_id: int,
    *,
    where: str,
    out_fields: str,
    fmt: str,
    return_geometry: bool,
    out_sr: int | None = None,
    order_by: str = "OBJECTID ASC",
) -> str:
    parts = [
        f"where={quote(where, safe='')}",
        f"outFields={quote(out_fields, safe=',')}",
        f"returnGeometry={'true' if return_geometry else 'false'}",
    ]
    if out_sr is not None:
        parts.append(f"outSR={out_sr}")
    if order_by:
        parts.append(f"orderByFields={quote(order_by, safe='')}")
    parts.append(f"f={fmt}")
    return f"{_ARCGIS_BASE}/{service}/FeatureServer/{layer_id}/query?" + "&".join(parts)


def _write_json_atomic(payload: dict[str, Any], dest: Path, url: str) -> base.DownloadResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = json.dumps(payload).encode("utf-8")
    tmp = dest.with_name(f"{dest.name}.part")
    tmp.write_bytes(raw_bytes)
    tmp.replace(dest)
    return base.DownloadResult(
        path=dest,
        url=url,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        size_bytes=len(raw_bytes),
    )


def _fetch_arcgis_paginated(
    http: httpx2.Client,
    base_url: str,
    dest: Path,
    *,
    feature_collection: bool,
) -> base.DownloadResult:
    """Paginate an ArcGIS FeatureServer query with ``resultOffset`` into one JSON file."""
    features: list[dict[str, Any]] = []
    offset = 0
    while True:
        sep = "&" if "?" in base_url else "?"
        page_url = f"{base_url}{sep}resultOffset={offset}&resultRecordCount={_PAGE_SIZE}"
        payload = base.get_with_retry(http, page_url).json()
        if "error" in payload:
            raise ValueError(f"ArcGIS query error for {page_url}: {payload['error']}")
        page_feats = payload.get("features", [])
        features.extend(page_feats)
        if not _has_more_arcgis_pages(payload, len(page_feats)):
            break
        offset += len(page_feats)

    merged: dict[str, Any] = (
        {"type": "FeatureCollection", "features": features}
        if feature_collection
        else {"features": features}
    )
    return _write_json_atomic(merged, dest, base_url)


def _resolve_first_layer_id(
    http: httpx2.Client, service: str, *, fallback: int = _FACILITIES_DEFAULT_LAYER_ID
) -> int:
    """Discover the active layer ID from a FeatureServer's metadata (handles layer-ID bumps)."""
    meta_url = f"{_ARCGIS_BASE}/{service}/FeatureServer?f=json"
    try:
        payload = base.get_with_retry(http, meta_url).json()
    except httpx2.HTTPError, ValueError:
        return fallback
    layers = payload.get("layers")
    if isinstance(layers, list) and layers and isinstance(layers[0], dict) and "id" in layers[0]:
        return int(layers[0]["id"])
    return fallback


def _discover_pprs_years(http: httpx2.Client, where: str) -> list[int]:
    url = _arcgis_query_url(
        _PPRS_SERVICE,
        0,
        where=where,
        out_fields="PERIODYR",
        fmt="json",
        return_geometry=False,
        order_by="PERIODYR ASC",
    )
    payload = base.get_with_retry(http, f"{url}&returnDistinctValues=true").json()
    if "error" in payload:
        raise ValueError(f"NSTA PPRS year discovery failed: {payload['error']}")
    years: list[int] = []
    for feat in payload.get("features", []):
        raw_yr = feat.get("attributes", {}).get("PERIODYR")
        if raw_yr is not None and str(raw_yr).strip().isdigit():
            years.append(int(str(raw_yr).strip()))
    return sorted(set(years))


def _read_raw_bytes(raw: base.RawFile) -> bytes:
    if isinstance(raw, (str, Path)):
        return Path(raw).read_bytes()
    return raw.read()


def _read_pprs_frame(raw: base.RawFile) -> pd.DataFrame:
    raw_bytes = _read_raw_bytes(raw)
    stripped = raw_bytes.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"{"):
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
        if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
            raise ValueError("not an NSTA PPRS JSON payload; expected 'features' list")
        records = [
            f["attributes"] if isinstance(f, dict) and "attributes" in f else f
            for f in payload["features"]
        ]
        return pd.DataFrame(records)
    return pd.read_csv(io.BytesIO(raw_bytes), encoding="utf-8-sig", low_memory=False)


def _build_pprs_period(frame: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    year_raw = frame[cols["periodyr"]]
    month_raw = frame[cols["periodmnth"]]
    period = pd.to_datetime(
        pd.DataFrame(
            {
                "year": pd.to_numeric(year_raw, errors="coerce"),
                "month": pd.to_numeric(month_raw, errors="coerce"),
                "day": 1,
            }
        ),
        errors="coerce",
    )
    non_blank = year_raw.notna() | month_raw.notna()
    bad = non_blank & period.isna()
    if bad.any():
        samples = frame.loc[bad, [cols["periodyr"], cols["periodmnth"]]].head(5).to_dict("records")
        raise ValueError(
            f"NSTA PPRS data has {int(bad.sum())} unparseable year/month row(s): {samples}"
        )
    return period


def _combine_gas_rates(
    frame: pd.DataFrame, cols: dict[str, str], days: pd.Series
) -> pd.Series | None:
    """Combine associated (``AGASPROKSM``) and dry (``DGASPROKSM``) gas (``ksm3`` -> ``e3m3/d``)."""
    agas = (
        pd.to_numeric(frame[cols["agasproksm"]], errors="coerce") if "agasproksm" in cols else None
    )
    dgas = (
        pd.to_numeric(frame[cols["dgasproksm"]], errors="coerce") if "dgasproksm" in cols else None
    )
    if agas is None and dgas is None:
        return None
    if agas is None:
        assert dgas is not None
        return dgas / days
    if dgas is None:
        return agas / days
    both_missing = agas.isna() & dgas.isna()
    total = agas.fillna(0.0) + dgas.fillna(0.0)
    return total.mask(both_missing, np.nan) / days


def _extract_pprs_rates(
    frame: pd.DataFrame, cols: dict[str, str], days: pd.Series
) -> dict[str, pd.Series]:
    m3_to_bpd = units.BBL_PER_M3 / days
    ksm3_to_e3m3d = 1.0 / days

    def scaled(col_key: str, factor: pd.Series) -> pd.Series | None:
        if col_key not in cols:
            return None
        return pd.to_numeric(frame[cols[col_key]], errors="coerce") * factor

    # Use INJWATVOL directly (NSTA's official injected-water volume); REINJWATVO is a
    # produced-water reinjection sub-metric reported as a subset of INJWATVOL by many
    # operators (e.g. Harding, Kraken, Alma) and cannot be summed without double-counting.
    candidates: dict[str, pd.Series | None] = {
        "oil_prod": scaled("oilprodm3", m3_to_bpd),
        "condensate_prod": scaled("gcondvol", m3_to_bpd),
        "gas_prod": _combine_gas_rates(frame, cols, days),
        "water_prod": scaled("watprodvol", m3_to_bpd),
        "water_inj": scaled("injwatvol", m3_to_bpd),
        "gas_inj": scaled("gasinjvol", ksm3_to_e3m3d),
    }
    return {k: v for k, v in candidates.items() if v is not None}


def _read_geo_with_env(path: Path, *, default_env: str) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    is_onshore = "onshore" in path.stem.lower() or "ons_county" in {c.lower() for c in gdf.columns}
    gdf = gdf.copy()
    gdf["_env"] = "onshore" if is_onshore else default_env
    return gdf


def _dissolve_fields_by_id(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    has_geom = gdf.geometry.notna() & ~gdf.geometry.is_empty
    with_geom = gdf.loc[has_geom].copy()
    if len(with_geom):
        with_geom = with_geom.set_geometry(with_geom.geometry.make_valid())
        dissolved = with_geom.dissolve(by="_fid", aggfunc="first").reset_index()
    else:
        dissolved = gdf.iloc[0:0].copy()

    bare = gdf.loc[~has_geom & ~gdf["_fid"].isin(dissolved["_fid"])].drop_duplicates("_fid").copy()
    if len(bare):
        bare = bare.set_geometry(gpd.GeoSeries([None] * len(bare), index=bare.index, crs=gdf.crs))
        combined = pd.concat([dissolved, bare], ignore_index=True)
        return gpd.GeoDataFrame(combined, geometry="geometry", crs=gdf.crs or schema.GEO_CRS)
    return dissolved


def _parse_fields_geojsons(paths: list[Path]) -> gpd.GeoDataFrame:
    frames = [_read_geo_with_env(p, default_env="offshore") for p in paths]
    gdf = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=schema.GEO_CRS
    )
    cols = {c.lower(): c for c in gdf.columns}
    if "fieldname" not in cols:
        raise ValueError(f"not an NSTA fields GeoJSON; columns: {sorted(gdf.columns)}")

    gdf["_fid"] = gdf[cols["fieldname"]].map(resolve_field_key).astype("string")
    gdf = gdf.loc[gdf["_fid"] != ""].copy()
    # Sort offshore first so cross-shoreline fields (e.g. Wytch Farm) keep offshore metadata.
    gdf["_env_rank"] = (gdf["_env"] != "offshore").astype(int)
    gdf = gdf.sort_values("_env_rank", kind="mergesort")
    dissolved = _dissolve_fields_by_id(gdf)

    field_name = _strip_fluid_suffix(dissolved[cols["fieldname"]])
    region = pd.Series(
        [("Onshore" if env == "onshore" else pd.NA) for env in dissolved["_env"]],
        index=dissolved.index,
        dtype="string",
    )
    out = gpd.GeoDataFrame(
        {
            "source": "uk",
            "field_id": dissolved["_fid"],
            "field_code": dissolved["_fid"],
            "field_name": field_name,
            "basin": pd.NA,
            "region": region,
            "environment": dissolved["_env"],
            "operator": (
                _clean_sentinel(dissolved[cols["curr_oper"]]) if "curr_oper" in cols else pd.NA
            ),
            "status": _clean_sentinel(dissolved[cols["status"]]) if "status" in cols else pd.NA,
            "primary_fluid": (
                _clean_sentinel(dissolved[cols["fieldtype"]]) if "fieldtype" in cols else pd.NA
            ),
            "discovery_date": (
                _parse_ym_date(dissolved[cols["disc_date"]]) if "disc_date" in cols else pd.NA
            ),
            "production_start_date": (
                _parse_ym_date(dissolved[cols["prod_date"]]) if "prod_date" in cols else pd.NA
            ),
            "water_depth_m": dissolved[cols["depth_m"]] if "depth_m" in cols else pd.NA,
        },
        geometry=dissolved.geometry,
        crs=dissolved.crs or schema.GEO_CRS,
    )
    return geo.prepare_geo(out, schema.GeoKind.FIELDS)


def _parse_facilities_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    cols = {c.lower(): c for c in gdf.columns}
    if "name" not in cols or ("feature_id" not in cols and "inf_type" not in cols):
        raise ValueError(
            f"not an NSTA surface infrastructure GeoJSON; columns: {sorted(gdf.columns)}"
        )

    if "curr_row" in cols:
        curr = gdf[cols["curr_row"]].astype("string").str.strip().str.upper()
        gdf = gdf.loc[curr != "N"].copy()

    name = gdf[cols["name"]].astype("string").str.strip()
    fid = (
        _clean_sentinel(gdf[cols["feature_id"]]).fillna(name.map(schema.normalize_key))
        if "feature_id" in cols
        else name.map(schema.normalize_key).astype("string")
    )
    out = gpd.GeoDataFrame(
        {
            "source": "uk",
            "facility_id": fid,
            "facility_code": fid,
            "facility_name": name,
            "facility_type": (
                _clean_sentinel(gdf[cols["inf_type"]]) if "inf_type" in cols else pd.NA
            ),
            "fields": pd.NA,
            "basin": pd.NA,
            "operator": (_clean_sentinel(gdf[cols["rep_group"]]) if "rep_group" in cols else pd.NA),
            "status": _clean_sentinel(gdf[cols["status"]]) if "status" in cols else pd.NA,
            "start_year": (
                _epoch_ms_to_year(gdf[cols["start_date"]]) if "start_date" in cols else pd.NA
            ),
            "export_system": (
                _clean_sentinel(gdf[cols["pipe_sys"]]) if "pipe_sys" in cols else pd.NA
            ),
        },
        geometry=gdf.geometry,
        crs=gdf.crs or schema.GEO_CRS,
    )
    out = out.loc[out["facility_id"].notna() & (out["facility_id"] != "")]
    out = out.drop_duplicates(subset=["facility_id"], keep="first")
    return geo.prepare_geo(out, schema.GeoKind.FACILITIES)


def _well_operator(gdf: gpd.GeoDataFrame, cols: dict[str, str]) -> pd.Series:
    sub = _clean_sentinel(gdf[cols["subareaop"]]) if "subareaop" in cols else None
    td = _clean_sentinel(gdf[cols["tdoperator"]]) if "tdoperator" in cols else None
    if sub is not None and td is not None:
        return sub.fillna(td)
    if sub is not None:
        return sub
    if td is not None:
        return td
    return pd.Series([pd.NA] * len(gdf), index=gdf.index, dtype="string")


def _well_status(gdf: gpd.GeoDataFrame, cols: dict[str, str]) -> pd.Series:
    raw = (
        _clean_sentinel(gdf[cols["complestat"]])
        if "complestat" in cols
        else _clean_sentinel(gdf[cols["wellopstat"]])
        if "wellopstat" in cols
        else pd.Series([pd.NA] * len(gdf), index=gdf.index, dtype="string")
    )
    if "wellopstat" in cols and "complestat" in cols:
        fallback = _clean_sentinel(gdf[cols["wellopstat"]])
        raw = raw.fillna(fallback)
    return raw.replace(_STATUS_FIXES).astype("string")


def _parse_wells_geojsons(paths: list[Path]) -> gpd.GeoDataFrame:
    frames = [_read_geo_with_env(p, default_env="offshore") for p in paths]
    gdf = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), geometry="geometry", crs=schema.GEO_CRS
    )
    cols = {c.lower(): c for c in gdf.columns}
    if "wellregno" not in cols:
        raise ValueError(f"not an NSTA wellbore top holes GeoJSON; columns: {sorted(gdf.columns)}")

    well_reg = gdf[cols["wellregno"]].astype("string").str.strip()
    target_fld = (
        _clean_sentinel(gdf[cols["targetfld"]])
        if "targetfld" in cols
        else pd.Series([pd.NA] * len(gdf), index=gdf.index, dtype="string")
    )
    field_id = target_fld.map(lambda v: resolve_field_key(v) if pd.notna(v) else pd.NA)
    field_id = field_id.mask(field_id == "", pd.NA).astype("string")

    out = gpd.GeoDataFrame(
        {
            "source": "uk",
            "well_id": well_reg,
            "well_name": well_reg,
            "field_id": field_id,
            "field_name": target_fld,
            "basin": pd.NA,
            "region": (_clean_sentinel(gdf[cols["ons_county"]]) if "ons_county" in cols else pd.NA),
            "environment": gdf["_env"],
            "operator": _well_operator(gdf, cols),
            "status": _well_status(gdf, cols),
            "spud_date": (_epoch_ms_to_iso(gdf[cols["spuddate"]]) if "spuddate" in cols else pd.NA),
            "completion_date": (
                _epoch_ms_to_iso(gdf[cols["compledate"]]) if "compledate" in cols else pd.NA
            ),
        },
        geometry=gdf.geometry,
        crs=gdf.crs or schema.GEO_CRS,
    )
    out = out.loc[out["well_id"].notna() & (out["well_id"] != "")]
    out = out.drop_duplicates(subset=["well_id"], keep="first")
    return geo.prepare_geo(out, schema.GeoKind.WELLS)


def _normalize_environment_filter(environment: str | None) -> str | None:
    if environment is None:
        return None
    norm = environment.strip().lower()
    if norm not in _ENVIRONMENTS:
        raise ValueError(f"uk environment must be 'offshore' or 'onshore', got {environment!r}")
    return norm


def _pprs_remote_for_year(year: int, env_norm: str | None) -> base.RemoteFile:
    clauses = [f"PERIODYR='{year}'"]
    suffix = ""
    if env_norm is not None:
        clauses.append(f"LOCATION='{_ENVIRONMENTS[env_norm]}'")
        suffix = f"_{env_norm}"
    url = _arcgis_query_url(
        _PPRS_SERVICE,
        0,
        where=" AND ".join(clauses),
        out_fields=_PPRS_OUT_FIELDS,
        fmt="json",
        return_geometry=False,
    )
    return base.RemoteFile(
        url=url,
        key=f"pprs_{year}{suffix}.json",
        year_min=year,
        year_max=year,
    )


@base.register
class UK(base.Source):
    """United Kingdom NSTA (North Sea Transition Authority) PPRS & Open Data Hub connector."""

    name = "uk"
    country = "United Kingdom"
    country_code = "GB"
    publisher = "NSTA"
    entity_type = schema.EntityType.FIELD
    docs_url = _DOCS_URL
    licence = (
        "NSTA User Agreement (non-commercial) — Contains information provided by"
        " the North Sea Transition Authority and/or other third parties."
    )
    licence_url = "https://www.nstauthority.co.uk/site-tools/access-to-information/"
    web_wells = True

    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[base.RemoteFile]:
        """Return one paginated ArcGIS query spec per reporting year."""
        env_norm = _normalize_environment_filter(environment)
        if year is not None:
            return [_pprs_remote_for_year(year, env_norm)]
        if recent:
            current_year = datetime.now(tz=UTC).year
            return [_pprs_remote_for_year(y, env_norm) for y in (current_year - 1, current_year)]

        where = f"LOCATION='{_ENVIRONMENTS[env_norm]}'" if env_norm is not None else "1=1"
        with base.open_client(client) as http:
            years = _discover_pprs_years(http, where)
        return [_pprs_remote_for_year(y, env_norm) for y in years]

    def fetch_production(
        self,
        remote: base.RemoteFile,
        dest: Path,
        *,
        client: httpx2.Client,
    ) -> base.DownloadResult:
        """Fetch all paginated PPRS records for one yearly query into ``dest``."""
        return _fetch_arcgis_paginated(client, remote.url, dest, feature_collection=False)

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        """Parse NSTA PPRS JSON or CSV into canonical daily rates (bbl/d, e3m3/d)."""
        frame = _read_pprs_frame(raw)
        if frame.empty:
            return schema.empty_production()

        cols = {c.lower(): c for c in frame.columns}
        missing = [c for c in _REQUIRED_PROD if c not in cols]
        if missing:
            raise ValueError(f"not an NSTA PPRS production file; missing {missing}")

        period = _build_pprs_period(frame, cols)
        days = period.dt.days_in_month.astype("float64")
        rates = _extract_pprs_rates(frame, cols, days)

        field_raw = frame[cols["fieldname"]].astype("string").str.strip()
        unit_raw = (
            frame[cols["unitname"]].astype("string").str.strip()
            if "unitname" in cols
            else field_raw
        )
        entity_name = unit_raw.mask(unit_raw == "", pd.NA).fillna(field_raw)
        entity_id = entity_name.map(schema.normalize_key).astype("string")
        field_id = field_raw.map(resolve_field_key).astype("string")

        return base.build_production_table(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            period=period,
            rates=rates,
            field_id=field_id,
            field_name=field_id,
            region=_normalize_area(frame[cols["fieldarea"]]) if "fieldarea" in cols else None,
            environment=_normalize_env(frame[cols["location"]]) if "location" in cols else None,
            operator=_clean_sentinel(frame[cols["orggrpnm"]]) if "orggrpnm" in cols else None,
        )

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Download WGS84 GeoJSON layers from the NSTA Open Data Hub."""
        with base.open_client(client) as http:
            if kind is schema.GeoKind.FIELDS:
                return [
                    _fetch_arcgis_paginated(
                        http,
                        _arcgis_query_url(
                            _OFFSHORE_FIELDS_SERVICE,
                            0,
                            where="1=1",
                            out_fields=_OFFSHORE_FIELDS_OUT,
                            fmt="geojson",
                            return_geometry=True,
                            out_sr=4326,
                        ),
                        dest_dir / "offshore_fields.geojson",
                        feature_collection=True,
                    ),
                    _fetch_arcgis_paginated(
                        http,
                        _arcgis_query_url(
                            _ONSHORE_FIELDS_SERVICE,
                            0,
                            where="1=1",
                            out_fields=_ONSHORE_FIELDS_OUT,
                            fmt="geojson",
                            return_geometry=True,
                            out_sr=4326,
                        ),
                        dest_dir / "onshore_fields.geojson",
                        feature_collection=True,
                    ),
                ]
            if kind is schema.GeoKind.FACILITIES:
                layer_id = _resolve_first_layer_id(http, _FACILITIES_SERVICE)
                return [
                    _fetch_arcgis_paginated(
                        http,
                        _arcgis_query_url(
                            _FACILITIES_SERVICE,
                            layer_id,
                            where="1=1",
                            out_fields=_FACILITIES_OUT,
                            fmt="geojson",
                            return_geometry=True,
                            out_sr=4326,
                        ),
                        dest_dir / "facilities.geojson",
                        feature_collection=True,
                    )
                ]
            if kind is schema.GeoKind.WELLS:
                return [
                    _fetch_arcgis_paginated(
                        http,
                        _arcgis_query_url(
                            _OFFSHORE_WELLS_SERVICE,
                            0,
                            where="1=1",
                            out_fields=_OFFSHORE_WELLS_OUT,
                            fmt="geojson",
                            return_geometry=True,
                            out_sr=4326,
                        ),
                        dest_dir / "offshore_wells.geojson",
                        feature_collection=True,
                    ),
                    _fetch_arcgis_paginated(
                        http,
                        _arcgis_query_url(
                            _ONSHORE_WELLS_SERVICE,
                            0,
                            where="1=1",
                            out_fields=_ONSHORE_WELLS_OUT,
                            fmt="geojson",
                            return_geometry=True,
                            out_sr=4326,
                        ),
                        dest_dir / "onshore_wells.geojson",
                        feature_collection=True,
                    ),
                ]
        raise ValueError(f"unsupported geo kind: {kind!r}")

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse NSTA GeoJSON files into a validated GeoParquet-ready GeoDataFrame."""
        if kind is schema.GeoKind.FIELDS:
            return _parse_fields_geojsons(paths)
        if kind is schema.GeoKind.FACILITIES:
            return _parse_facilities_geojson(paths[0])
        if kind is schema.GeoKind.WELLS:
            return _parse_wells_geojsons(paths)
        raise ValueError(f"unsupported geo kind: {kind!r}")
