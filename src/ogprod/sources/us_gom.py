"""USA — Federal Offshore Gulf of Mexico (BSEE / BOEM OGOR-A) connector.

BSEE and BOEM publish monthly well/completion-level production for the US Gulf
Outer Continental Shelf (OCS) from 1996 to present via direct HTTPS ZIP archives
in the public domain (no login or API key):

- Production (`OGOR-A`, 1996-present, well grain):
  - Fixed 19-column headerless comma-delimited ASCII (`ogora{YYYY}delimit.zip`
    plus rolling current-year `ogoradelimit.zip`, regenerated bi-monthly).
  - Oil (`MON_O_PROD_VOL`, bbl) -> ``bbl/d``, Gas (`MON_G_PROD_VOL`, Mcf) ->
    ``e3m3/d``, Produced water (`MON_WTR_PROD_VOL`, bbl) -> ``bbl/d``.
  - OGOR-A reports per completion/interval; multiple completions on the same
    well are summed to ``(API_WELL_NUMBER, field_id, period)`` and ~40%
    all-zero status rows are dropped. ``INJECTION_VOLUME`` is ignored because
    OGOR-A does not distinguish injected fluid type (water vs gas).
- Fields (`geo/fields`):
  - BOEM does not publish geological field polygons directly; instead, we join
    BOEM's official Field-to-Block master list (`mastdatadelimit.zip`) against
    clipped OCS Block polygons (`blk_clip.zip`, NAD 27 ``EPSG:4267``) and
    dissolve by ``FIELD_NAME_CODE``, enriching field names with official BSEE
    nicknames (`appendcdelimit.zip`, e.g. ``Mars (MC807)``). Unmatched fields
    remain as ``geometry = NULL`` metadata rows.
- Facilities (`geo/facilities`):
  - BSEE Platform Structures (`PlatStrucRawData.zip` ->
    ``mv_platstruc_structures.txt``): fixed platforms, caissons, spars, TLPs,
    semisubmersibles, and FPSOs with water depth (ft -> m), structure type,
    operator, install/removal dates, and NAD 27 coordinates.
- Wells (`geo/wells`):
  - BSEE Boreholes (`BoreholeRawData.zip` -> ``mv_boreholes_all.txt``), filtered
    to Gulf OCS (``REGION_CODE == "G"``) with 12-digit ``API_WELL_NUMBER``,
    surface NAD 27 coordinates, status, operator, and spud/TD dates.

All Gulf spatial coordinates are published in NAD 27 (``EPSG:4267``) and
reprojected explicitly to ``EPSG:4326`` via :func:`ogprod.geo.prepare_geo`.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa
import shapely

from ogprod import geo, schema, units
from ogprod.sources import base

_BASE_URL = "https://www.data.bsee.gov"
_OGORA_INDEX_URL = f"{_BASE_URL}/Main/OGOR-A.aspx"
_DOCS_URL = _OGORA_INDEX_URL
_LICENCE_URL = f"{_BASE_URL}/Main/Disclaimer.aspx"

_BOREHOLES_URL = f"{_BASE_URL}/Well/Files/BoreholeRawData.zip"
_PLATFORMS_URL = f"{_BASE_URL}/Platform/Files/PlatStrucRawData.zip"
_BLOCKS_SHP_URL = f"{_BASE_URL}/Mapping/Files/blk_clip.zip"
_MASTDATA_URL = f"{_BASE_URL}/FieldReserves/Files/mastdatadelimit.zip"
_APPENDC_URL = f"{_BASE_URL}/FieldReserves/Files/appendcdelimit.zip"

_NAD27_CRS = "EPSG:4267"
_CONUS_ALBERS_CRS = "EPSG:5070"
_FEET_TO_METERS = 0.3048
_MCF_TO_E3M3 = units.volume_factor("mcf", "e3m3")

FIRST_YEAR = 1996
_RECENT_YEARS = 3

_YEARLY_ZIP_RE = re.compile(
    r'href=["\']([^"\']*?/Production/Files/ogora(\d{4})delimit\.zip)["\']',
    re.IGNORECASE,
)
_CURRENT_ZIP_RE = re.compile(
    r'href=["\']([^"\']*?/Production/Files/ogoradelimit\.zip)["\']',
    re.IGNORECASE,
)

# Fixed 19-column headerless layout from BSEE's OGOR-A field definitions.
_OGORA_COLUMNS: tuple[str, ...] = (
    "LEASE_NUMBER",
    "COMPLETION_NAME",
    "PRODUCTION_DATE",
    "DAYS_ON_PROD",
    "PRODUCT_CODE",
    "MON_O_PROD_VOL",
    "MON_G_PROD_VOL",
    "MON_WTR_PROD_VOL",
    "API_WELL_NUMBER",
    "WELL_STAT_CD",
    "AREA_BLOCK",
    "OPERATOR_NUM",
    "SORT_NAME",
    "FIELD_NAME_CODE",
    "INJECTION_VOLUME",
    "PROD_INTERVAL_CD",
    "FIRST_PROD_DATE",
    "UNIT_AGT_NUMBER",
    "UNIT_ALOC_SUFFIX",
)

# Official BOEM Gulf OCS area code abbreviations -> human-readable area names.
_AREA_NAMES: dict[str, str] = {
    "AC": "Alaminos Canyon",
    "AM": "Amery Terrace",
    "AP": "Apalachicola",
    "AT": "Atwater Valley",
    "BA": "Brazos",
    "BM": "Bay Marchand",
    "BS": "Breton Sound",
    "CA": "Chandeleur",
    "CC": "Corpus Christi",
    "CE": "Chandeleur East",
    "CH": "Charlotte Harbor",
    "DC": "De Soto Canyon",
    "DD": "Destin Dome",
    "DT": "Dry Tortugas",
    "EB": "East Breaks",
    "EC": "East Cameron",
    "EI": "Eugene Island",
    "EL": "Elbow",
    "EW": "Ewing Bank",
    "FM": "Florida Middle Ground",
    "FP": "Florida Plain",
    "GA": "Galveston",
    "GB": "Garden Banks",
    "GC": "Green Canyon",
    "GI": "Grand Isle",
    "GV": "Gainesville",
    "HE": "Henderson",
    "HH": "Howell Hook",
    "HI": "High Island",
    "KC": "Keathley Canyon",
    "KW": "Key West",
    "LL": "Lloyd Ridge",
    "LS": "Lund South",
    "LU": "Lund",
    "MA": "Matagorda Island",
    "MC": "Mississippi Canyon",
    "MI": "Mustang Island",
    "MO": "Mobile",
    "MP": "Main Pass",
    "MU": "Mustang Island",
    "PB": "St. Petersburg",
    "PE": "Pensacola",
    "PI": "Port Isabel",
    "PL": "South Pelto",
    "PN": "North Padre Island",
    "PR": "Pulley Ridge",
    "PS": "South Padre Island",
    "RK": "Rankin",
    "SA": "Sabine Pass",
    "SE": "Sabine Pass East",
    "SM": "South Marsh Island",
    "SP": "South Pass",
    "SS": "Ship Shoal",
    "ST": "South Timbalier",
    "SX": "Sigsbee Escarpment",
    "TP": "Tarpon Springs",
    "TV": "Tortugas Valley",
    "VK": "Viosca Knoll",
    "VN": "Vernon",
    "VR": "Vermilion",
    "WC": "West Cameron",
    "WD": "West Delta",
    "WR": "Walker Ridge",
}

_FACILITY_TYPE_LABELS: dict[str, str] = {
    "CAIS": "Caisson",
    "CT": "Compliant Tower",
    "FIXED": "Fixed Platform",
    "FPSO": "FPSO",
    "MOPU": "MOPU",
    "MTLP": "Mini-TLP",
    "SEMI": "Semisubmersible",
    "SPAR": "Spar",
    "TLP": "TLP",
    "WP": "Well Protector",
}

_BOREHOLE_STATUS_LABELS: dict[str, str] = {
    "AST": "Approved Sidetrack",
    "CNL": "Cancelled",
    "COM": "Completed",
    "DRL": "Drilling",
    "PA": "Plugged & Abandoned",
    "ST": "Sidetracked",
    "TA": "Temporarily Abandoned",
}

_GULF_LON_MIN = -98.5
_GULF_LON_MAX = -80.0
_GULF_LAT_MIN = 23.0
_GULF_LAT_MAX = 31.0


def _abs_url(href: str) -> str:
    if href.startswith(("http://", "https://")):
        return href
    return f"{_BASE_URL}/{href.lstrip('/')}"


def _expand_area_code(raw: str | None) -> str | None:
    if raw is None or pd.isna(raw):
        return None
    text = str(raw).strip().upper()
    if not text:
        return None
    code = text.split()[0][:2]
    return _AREA_NAMES.get(code, code)


def _normalize_block_num(raw: str | None) -> str:
    if raw is None or pd.isna(raw):
        return ""
    return re.sub(r"\s+", " ", str(raw).strip().upper())


def _parse_mdy_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format="%m/%d/%Y", errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").astype("string")


def _parse_mon_year_date(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format="%b-%Y", errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").astype("string")


def _open_zip_member(path: Path, suffix: str, *, contains: str | None = None) -> bytes:
    with zipfile.ZipFile(path) as zf:
        candidates = [
            name
            for name in zf.namelist()
            if name.lower().endswith(suffix)
            and (contains is None or contains.lower() in name.lower())
        ]
        if not candidates:
            raise ValueError(f"us-gom: no {suffix!r} member found in {path.name}")
        return zf.read(candidates[0])


def _read_ogora_bytes(raw: base.RawFile) -> bytes:
    if isinstance(raw, (str, Path)):
        path = Path(raw)
        if zipfile.is_zipfile(path):
            return _open_zip_member(path, ".txt")
        return path.read_bytes()
    data = raw.read()
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [n for n in zf.namelist() if n.lower().endswith(".txt")]
            if not members:
                raise ValueError("us-gom: no .txt member found in OGOR-A zip stream")
            return zf.read(members[0])
    return data


def _read_ogora_frame(data: bytes) -> pd.DataFrame:
    if not data.strip():
        return pd.DataFrame(columns=list(_OGORA_COLUMNS))
    df = pd.read_csv(
        io.BytesIO(data),
        header=None,
        dtype=str,
        keep_default_na=False,
        encoding="latin1",
    )
    if df.shape[1] != len(_OGORA_COLUMNS):
        raise ValueError(
            f"not a BSEE OGOR-A file: expected {len(_OGORA_COLUMNS)} columns, got {df.shape[1]}"
        )
    df.columns = list(_OGORA_COLUMNS)
    sample_dates = df["PRODUCTION_DATE"].str.strip().head(20)
    if not sample_dates.str.fullmatch(r"\d{6}").all():
        raise ValueError("not a BSEE OGOR-A file: PRODUCTION_DATE must be YYYYMM")
    return df


def _first_non_empty(series: pd.Series) -> str:
    for val in series:
        if val:
            return str(val)
    return ""


def _parse_ogora_production(raw: base.RawFile) -> pa.Table:
    df = _read_ogora_frame(_read_ogora_bytes(raw))
    if df.empty:
        return schema.empty_production()

    for col in (
        "PRODUCTION_DATE",
        "API_WELL_NUMBER",
        "AREA_BLOCK",
        "SORT_NAME",
        "FIELD_NAME_CODE",
    ):
        df[col] = df[col].str.strip()

    oil_bbl = pd.to_numeric(df["MON_O_PROD_VOL"].str.strip(), errors="coerce").fillna(0.0)
    gas_mcf = pd.to_numeric(df["MON_G_PROD_VOL"].str.strip(), errors="coerce").fillna(0.0)
    water_bbl = pd.to_numeric(df["MON_WTR_PROD_VOL"].str.strip(), errors="coerce").fillna(0.0)

    keep = (oil_bbl > 0) | (gas_mcf > 0) | (water_bbl > 0)
    if not keep.any():
        return schema.empty_production()

    work = pd.DataFrame(
        {
            "entity_id": df.loc[keep, "API_WELL_NUMBER"].values,
            "field_code": df.loc[keep, "FIELD_NAME_CODE"].values,
            "period_ym": df.loc[keep, "PRODUCTION_DATE"].values,
            "operator": df.loc[keep, "SORT_NAME"].values,
            "area_block": df.loc[keep, "AREA_BLOCK"].values,
            "oil_bbl": oil_bbl.loc[keep].to_numpy(dtype=np.float64),
            "gas_mcf": gas_mcf.loc[keep].to_numpy(dtype=np.float64),
            "water_bbl": water_bbl.loc[keep].to_numpy(dtype=np.float64),
        }
    )
    work["field_id"] = work["field_code"].map(schema.normalize_key)

    grouped = work.groupby(["entity_id", "field_id", "period_ym"], as_index=False, sort=False).agg(
        field_code=("field_code", _first_non_empty),
        operator=("operator", _first_non_empty),
        area_block=("area_block", _first_non_empty),
        oil_bbl=("oil_bbl", "sum"),
        gas_mcf=("gas_mcf", "sum"),
        water_bbl=("water_bbl", "sum"),
    )

    periods = pd.to_datetime(grouped["period_ym"] + "01", format="%Y%m%d", errors="coerce")
    days = periods.dt.days_in_month.astype("float64")
    field_name = grouped["field_code"].mask(grouped["field_code"] == "", pd.NA)
    region = grouped["area_block"].map(_expand_area_code)
    environment = pd.Series(["offshore"] * len(grouped), dtype="string")

    return base.build_production_table(
        source="us-gom",
        entity_type=schema.EntityType.WELL,
        entity_id=grouped["entity_id"],
        entity_name=grouped["entity_id"],
        field_id=grouped["field_id"],
        field_name=field_name,
        region=region,
        environment=environment,
        operator=grouped["operator"],
        period=periods,
        rates={
            "oil_prod": grouped["oil_bbl"] / days,
            "gas_prod": grouped["gas_mcf"] * _MCF_TO_E3M3 / days,
            "water_prod": grouped["water_bbl"] / days,
        },
    )


def _discover_from_html(html: str, *, current_year: int) -> list[base.RemoteFile]:
    by_year: dict[int, str] = {}
    for href, year_s in _YEARLY_ZIP_RE.findall(html):
        yr = int(year_s)
        if yr >= FIRST_YEAR:
            by_year[yr] = _abs_url(href)

    files = [
        base.RemoteFile(url=url, key=f"ogora_{yr}", year_min=yr, year_max=yr)
        for yr, url in sorted(by_year.items())
    ]

    cur_match = _CURRENT_ZIP_RE.search(html)
    if cur_match is not None:
        next_yr = max(by_year) + 1 if by_year else current_year
        files.append(
            base.RemoteFile(
                url=_abs_url(cur_match.group(1)),
                key="ogora_current",
                year_min=next_yr,
                year_max=max(current_year, next_yr),
            )
        )
    return files


def _read_delimited_bytes(path: Path, *, contains: str | None = None) -> bytes:
    if zipfile.is_zipfile(path):
        return _open_zip_member(path, ".txt", contains=contains)
    return path.read_bytes()


def _parse_appendc_nicknames(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = _read_delimited_bytes(path)
    if not data.strip():
        return {}
    df = pd.read_csv(
        io.BytesIO(data),
        header=None,
        dtype=str,
        keep_default_na=False,
        encoding="latin1",
    )
    if df.shape[1] < 2:
        return {}
    nicknames: dict[str, str] = {}
    for nick, code in zip(df[0].str.strip(), df[1].str.strip().str.upper(), strict=True):
        if nick and code and code not in nicknames:
            nicknames[code] = nick.title()
    return nicknames


def _parse_mastdata(path: Path) -> pd.DataFrame:
    data = _read_delimited_bytes(path)
    if not data.strip():
        return pd.DataFrame(
            columns=["field_code", "area_code", "block_num", "operator", "eff_date", "term_status"]
        )
    df = pd.read_csv(
        io.BytesIO(data),
        header=None,
        dtype=str,
        keep_default_na=False,
        encoding="latin1",
    )
    if df.shape[1] < 9:
        raise ValueError(f"us-gom: unexpected mastdata column count {df.shape[1]}")
    out = pd.DataFrame(
        {
            "field_code": df[0].str.strip().str.upper(),
            "area_code": df[2].str.strip().str.upper(),
            "block_num": df[3].map(_normalize_block_num),
            "operator": df[5].str.strip(),
            "eff_date": _parse_mon_year_date(df[6].str.strip()),
            "term_status": df[8].str.strip().str.upper(),
        }
    )
    return out.loc[out["field_code"] != ""].reset_index(drop=True)


def _read_blocks_shapefile(path: Path) -> gpd.GeoDataFrame:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            shp_members = [n for n in zf.namelist() if n.lower().endswith(".shp")]
            if not shp_members:
                raise ValueError(f"us-gom: no .shp member found in {path.name}")
            uri = f"zip://{path}!{shp_members[0]}"
        gdf = gpd.read_file(uri)
    else:
        gdf = gpd.read_file(path)

    area_col = "AREA_CODE" if "AREA_CODE" in gdf.columns else "area_code"
    block_col = "BLOCK_NUMB" if "BLOCK_NUMB" in gdf.columns else "block_numb"
    gdf["area_code"] = gdf[area_col].astype("string").str.strip().str.upper()
    gdf["block_num"] = gdf[block_col].map(_normalize_block_num)
    return gpd.GeoDataFrame(
        gdf[["area_code", "block_num", "geometry"]],
        geometry="geometry",
        crs=gdf.crs or _NAD27_CRS,
    )


def _format_field_name(code: str, nicknames: dict[str, str]) -> str:
    nick = nicknames.get(code)
    return f"{nick} ({code})" if nick else code


def _summarize_mastdata_fields(mastdata: pd.DataFrame, nicknames: dict[str, str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for code, grp in mastdata.groupby("field_code", sort=True):
        code_s = str(code)
        is_active = bool((grp["term_status"] == "").any())
        active_ops = grp.loc[grp["term_status"] == "", "operator"]
        op_source = active_ops if len(active_ops) else grp["operator"]
        operator = _first_non_empty(op_source) or None
        dates = grp["eff_date"].dropna()
        discovery_date = str(dates.min()) if len(dates) else None
        area_mode = grp["area_code"].mode()
        dominant_area = str(area_mode.iloc[0]) if len(area_mode) else None
        rows.append(
            {
                "source": "us-gom",
                "field_id": schema.normalize_key(code_s),
                "field_code": code_s,
                "field_name": _format_field_name(code_s, nicknames),
                "basin": None,
                "region": _expand_area_code(dominant_area),
                "environment": "offshore",
                "operator": operator,
                "status": "Active" if is_active else "Expired",
                "discovery_date": discovery_date,
            }
        )
    return pd.DataFrame(rows)


def _classify_field_paths(paths: list[Path]) -> tuple[Path, Path, Path | None]:
    blocks_path: Path | None = None
    mastdata_path: Path | None = None
    appendc_path: Path | None = None
    for p in paths:
        lower = p.name.lower()
        if "blk" in lower or "block" in lower or p.suffix.lower() == ".shp":
            blocks_path = p
        elif "mast" in lower:
            mastdata_path = p
        elif "appendc" in lower:
            appendc_path = p
    if blocks_path is None or mastdata_path is None:
        raise ValueError("us-gom: parse_geo(FIELDS) requires both blk_clip and mastdata files")
    return blocks_path, mastdata_path, appendc_path


def _parse_fields(paths: list[Path]) -> gpd.GeoDataFrame:
    blocks_path, mastdata_path, appendc_path = _classify_field_paths(paths)
    mastdata = _parse_mastdata(mastdata_path)
    if mastdata.empty:
        return schema.empty_geo(schema.GeoKind.FIELDS)

    nicknames = _parse_appendc_nicknames(appendc_path)
    blocks = _read_blocks_shapefile(blocks_path)
    field_meta = _summarize_mastdata_fields(mastdata, nicknames)

    block_links = mastdata[["field_code", "area_code", "block_num"]].drop_duplicates()
    joined = block_links.merge(blocks, on=["area_code", "block_num"], how="inner")

    geom_by_code: dict[str, shapely.Geometry] = {}
    area_by_code: dict[str, float] = {}
    if not joined.empty:
        joined_gdf = gpd.GeoDataFrame(joined, geometry="geometry", crs=blocks.crs)
        dissolved = joined_gdf.dissolve(by="field_code", as_index=False)
        invalid = ~dissolved.geometry.is_valid
        if invalid.any():
            dissolved.loc[invalid, "geometry"] = dissolved.loc[invalid, "geometry"].make_valid()
        areas_km2 = dissolved.to_crs(_CONUS_ALBERS_CRS).geometry.area / 1_000_000.0
        for code, geom, area in zip(
            dissolved["field_code"], dissolved.geometry, areas_km2, strict=True
        ):
            if geom is not None and not geom.is_empty:
                geom_by_code[str(code)] = geom
                area_by_code[str(code)] = float(area)

    field_meta["area_km2"] = field_meta["field_code"].map(area_by_code)
    geoms = [geom_by_code.get(str(c)) for c in field_meta["field_code"]]
    gdf = gpd.GeoDataFrame(field_meta, geometry=geoms, crs=blocks.crs)
    return geo.prepare_geo(gdf, schema.GeoKind.FIELDS, crs=_NAD27_CRS)


def _valid_gulf_points(lon: pd.Series, lat: pd.Series) -> list[shapely.Point | None]:
    lon_num = pd.to_numeric(lon, errors="coerce")
    lat_num = pd.to_numeric(lat, errors="coerce")
    valid = (
        lon_num.notna()
        & lat_num.notna()
        & (lon_num >= _GULF_LON_MIN)
        & (lon_num <= _GULF_LON_MAX)
        & (lat_num >= _GULF_LAT_MIN)
        & (lat_num <= _GULF_LAT_MAX)
    )
    return [
        shapely.Point(float(x), float(y)) if ok else None
        for x, y, ok in zip(lon_num, lat_num, valid, strict=True)
    ]


def _format_facility_name(area: str, block: str, struct_name: str, complex_id: str) -> str:
    parts = [p for p in (area, block, struct_name) if p]
    return " ".join(parts) if parts else complex_id


def _parse_facilities(paths: list[Path]) -> gpd.GeoDataFrame:
    if not paths:
        return schema.empty_geo(schema.GeoKind.FACILITIES)
    data = _read_delimited_bytes(paths[0], contains="mv_platstruc_structures")
    if not data.strip():
        return schema.empty_geo(schema.GeoKind.FACILITIES)

    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="latin1")
    for col in (
        "COMPLEX_ID_NUM",
        "STRUCTURE_NUMBER",
        "AREA_CODE",
        "BLOCK_NUMBER",
        "STRUCTURE_NAME",
        "STRUC_TYPE_CODE",
        "FIELD_NAME_CODE",
        "BUS_ASC_NAME",
        "INSTALL_DATE",
        "REMOVAL_DATE",
        "WATER_DEPTH",
        "LATITUDE",
        "LONGITUDE",
    ):
        if col in df.columns:
            df[col] = df[col].str.strip()

    facility_id = df["COMPLEX_ID_NUM"] + "-" + df["STRUCTURE_NUMBER"]
    df = df.assign(_fid=facility_id).drop_duplicates(subset=["_fid"], keep="first")

    names = [
        _format_facility_name(a, b, s, c)
        for a, b, s, c in zip(
            df["AREA_CODE"],
            df["BLOCK_NUMBER"],
            df["STRUCTURE_NAME"],
            df["COMPLEX_ID_NUM"],
            strict=True,
        )
    ]
    fields_col = df["FIELD_NAME_CODE"].mask(df["FIELD_NAME_CODE"].isin(["", "WILD"]), pd.NA)
    install_dates = pd.to_datetime(df["INSTALL_DATE"], format="%m/%d/%Y", errors="coerce")
    status = np.where(df["REMOVAL_DATE"] != "", "Removed", "Active")
    depth_ft = pd.to_numeric(df["WATER_DEPTH"], errors="coerce")
    depth_m = depth_ft.where(depth_ft > 0) * _FEET_TO_METERS

    out = pd.DataFrame(
        {
            "source": "us-gom",
            "facility_id": df["_fid"],
            "facility_code": df["COMPLEX_ID_NUM"],
            "facility_name": names,
            "facility_type": df["STRUC_TYPE_CODE"].map(
                lambda c: _FACILITY_TYPE_LABELS.get(c, c or None)
            ),
            "fields": fields_col,
            "operator": df["BUS_ASC_NAME"],
            "status": status,
            "start_year": install_dates.dt.year,
            "water_depth_m": depth_m,
        }
    )
    geoms = _valid_gulf_points(df["LONGITUDE"], df["LATITUDE"])
    gdf = gpd.GeoDataFrame(out, geometry=geoms, crs=_NAD27_CRS)
    return geo.prepare_geo(gdf, schema.GeoKind.FACILITIES, crs=_NAD27_CRS)


def _format_well_name(name: str, suffix: str, api: str) -> str:
    combined = " ".join(p for p in (name, suffix) if p)
    return combined or api


def _parse_wells(paths: list[Path]) -> gpd.GeoDataFrame:
    if not paths:
        return schema.empty_geo(schema.GeoKind.WELLS)
    data = _read_delimited_bytes(paths[0], contains="mv_boreholes_all")
    if not data.strip():
        return schema.empty_geo(schema.GeoKind.WELLS)

    df = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="latin1")
    for col in (
        "API_WELL_NUMBER",
        "WELL_NAME",
        "WELL_NAME_SUFFIX",
        "BOTM_AREA_CODE",
        "COMPANY_NAME",
        "WELL_SPUD_DATE",
        "TOTAL_DEPTH_DATE",
        "BOREHOLE_STAT_CD",
        "SURF_LATITUDE",
        "SURF_LONGITUDE",
        "REGION_CODE",
    ):
        if col in df.columns:
            df[col] = df[col].str.strip()

    if "REGION_CODE" in df.columns:
        df = df.loc[df["REGION_CODE"] == "G"].copy()
    df = df.loc[df["API_WELL_NUMBER"] != ""].drop_duplicates(
        subset=["API_WELL_NUMBER"], keep="first"
    )
    if df.empty:
        return schema.empty_geo(schema.GeoKind.WELLS)

    well_names = [
        _format_well_name(n, s, api)
        for n, s, api in zip(
            df["WELL_NAME"], df["WELL_NAME_SUFFIX"], df["API_WELL_NUMBER"], strict=True
        )
    ]
    out = pd.DataFrame(
        {
            "source": "us-gom",
            "well_id": df["API_WELL_NUMBER"],
            "well_name": well_names,
            "region": df["BOTM_AREA_CODE"].map(_expand_area_code),
            "environment": "offshore",
            "operator": df["COMPANY_NAME"],
            "status": df["BOREHOLE_STAT_CD"].map(
                lambda c: _BOREHOLE_STATUS_LABELS.get(c, c or None)
            ),
            "spud_date": _parse_mdy_date(df["WELL_SPUD_DATE"]),
            "completion_date": _parse_mdy_date(df["TOTAL_DEPTH_DATE"]),
        }
    )
    geoms = _valid_gulf_points(df["SURF_LONGITUDE"], df["SURF_LATITUDE"])
    gdf = gpd.GeoDataFrame(out, geometry=geoms, crs=_NAD27_CRS)
    return geo.prepare_geo(gdf, schema.GeoKind.WELLS, crs=_NAD27_CRS)


@base.register
class UsGom(base.Source):
    """US Federal Offshore Gulf of Mexico connector (BSEE / BOEM OGOR-A)."""

    name = "us-gom"
    country = "US (GoM)"
    country_code = "US"
    publisher = "BSEE / BOEM (OGOR-A)"
    entity_type = schema.EntityType.WELL
    docs_url = _DOCS_URL
    licence = "US Federal Government Public Domain"
    licence_url = _LICENCE_URL
    web_wells = True
    search_all_wells = False

    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[base.RemoteFile]:
        if environment is not None and environment.lower() == "onshore":
            return []
        current_year = datetime.now(tz=UTC).year
        with base.open_client(client) as http:
            resp = base.get_with_retry(http, _OGORA_INDEX_URL)
            files = _discover_from_html(resp.text, current_year=current_year)
        if year is not None:
            files = [
                f
                for f in files
                if f.year_min is not None
                and f.year_max is not None
                and f.year_min <= year <= f.year_max
            ]
        elif recent and files:
            max_yr = max(f.year_max for f in files if f.year_max is not None)
            cutoff = max_yr - (_RECENT_YEARS - 1)
            files = [f for f in files if f.year_max is not None and f.year_max >= cutoff]
        return files

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        return _parse_ogora_production(raw)

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            if kind is schema.GeoKind.FIELDS:
                return [
                    base.download_file(http, _BLOCKS_SHP_URL, dest_dir / "blk_clip.zip"),
                    base.download_file(http, _MASTDATA_URL, dest_dir / "mastdatadelimit.zip"),
                    base.download_file(http, _APPENDC_URL, dest_dir / "appendcdelimit.zip"),
                ]
            if kind is schema.GeoKind.FACILITIES:
                return [
                    base.download_file(http, _PLATFORMS_URL, dest_dir / "PlatStrucRawData.zip"),
                ]
            if kind is schema.GeoKind.WELLS:
                return [
                    base.download_file(http, _BOREHOLES_URL, dest_dir / "BoreholeRawData.zip"),
                ]
        raise NotImplementedError(f"unsupported geo kind: {kind}")  # pragma: no cover

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        if kind is schema.GeoKind.FIELDS:
            return _parse_fields(paths)
        if kind is schema.GeoKind.FACILITIES:
            return _parse_facilities(paths)
        if kind is schema.GeoKind.WELLS:
            return _parse_wells(paths)
        raise NotImplementedError(f"unsupported geo kind: {kind}")  # pragma: no cover


def parse_production(raw: base.RawFile) -> pa.Table:
    """Parse a BSEE OGOR-A delimited ASCII or ZIP file into a canonical production table."""
    return _parse_ogora_production(raw)


def parse_fields(paths: list[Path]) -> gpd.GeoDataFrame:
    """Parse BOEM ``blk_clip.zip``, ``mastdatadelimit.zip``, and optional ``appendcdelimit.zip``."""
    return _parse_fields(paths)


def parse_facilities(paths: list[Path]) -> gpd.GeoDataFrame:
    """Parse BSEE ``PlatStrucRawData.zip`` (or ``mv_platstruc_structures.txt``) into facilities."""
    return _parse_facilities(paths)


def parse_wells(paths: list[Path]) -> gpd.GeoDataFrame:
    """Parse BSEE ``BoreholeRawData.zip`` (or ``mv_boreholes_all.txt``) into Gulf wells."""
    return _parse_wells(paths)
