"""Netherlands — NLOG (TNO / Geological Survey of the Netherlands) connector.

NLOG publishes monthly well-level production and injection back to January 2003
under the Mining Decree (Mbb art. 111.1 / 112.1) via the NLOG Datacenter REST API,
plus all three spatial layers via the GDN GeoServices OGC WFS (ED50 / UTM 31N,
``EPSG:23031``):

- Production & injection (2003-present, onshore + Dutch North Sea, well grain):
  - Natural gas produced & injected: reported in ``1000 Nm³`` (at 0 °C, 101.325 kPa)
    -> stored directly as ``e3m3/d`` (``1000 Nm³ / days_in_month``; note the ~5.5%
    reference-temperature difference vs 15 °C ``Sm³`` used by Sodir/NSTA, preserved
    without synthetic rescaling to match official NLOG figures).
  - Crude oil & condensate produced: reported in ``Sm³`` -> ``bbl/d``.
  - Produced water & injected water: reported in ``m³`` -> ``bbl/d``.
- Fields (``nlog:gdw_ng_field_utm``): onshore and Dutch North Sea hydrocarbon field
  polygons (reprojected from ``EPSG:23031`` to ``EPSG:4326`` with geometry repair).
- Facilities (``nlog:GDW_NG_FACILITY_UTM``): hydrocarbon mining installations
  (platforms, satellites, subsea structures, production locations; geothermal
  installations ``GTI`` excluded).
- Wells (``nlog:gdw_ng_wll_all_utm``): hydrocarbon/storage boreholes deduplicated
  to canonical ``WELL_NAME`` (falling back to ``BOREHOLE_NAME``) so producing wells
  join cleanly to ``production.entity_id``.

Data source: NLOG.NL (TNO - Geological Survey of the Netherlands / Ministry of
Climate Policy and Green Growth). Free reuse and redistribution with attribution
to NLOG.NL (https://www.nlog.nl/en/disclaimer).
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa

from ogprod import geo, schema, units
from ogprod.sources import base

_DOCS_URL = "https://www.nlog.nl/en/production-and-injection-data"
_LICENCE_URL = "https://www.nlog.nl/en/disclaimer"
_PROD_WELL_URL = "https://www.nlog.nl/nlog-mapviewer-api/rest/prodfigures/well"
_WFS_URL = "https://www.gdngeoservices.nl/geoserver/nlog/ows"
_NLOG_CRS = "EPSG:23031"

FIRST_YEAR = 2003
_HISTORY_CHUNK_YEARS = 5

_MONTH_KEYS: tuple[tuple[str, int], ...] = (
    ("jan", 1),
    ("feb", 2),
    ("mar", 3),
    ("apr", 4),
    ("may", 5),
    ("jun", 6),
    ("jul", 7),
    ("aug", 8),
    ("sep", 9),
    ("oct", 10),
    ("nov", 11),
    ("dec", 12),
)

#: Canonical rate column -> (NLOG product, NLOG production type, unit kind).
_STREAMS: tuple[tuple[str, str, str, str], ...] = (
    ("gas_prod", "Gas", "Produced", "gas"),
    ("oil_prod", "Oil", "Produced", "liquid"),
    ("condensate_prod", "Condensate", "Produced", "liquid"),
    ("water_prod", "Water", "Produced", "liquid"),
    ("water_inj", "Water", "Injected", "liquid"),
    ("gas_inj", "Gas", "Injected", "gas"),
)

_ENV_TO_LOC: dict[str, str] = {
    "onshore": "L",
    "offshore": "S",
}
_LOC_TO_ENV: dict[str, str] = {
    "L": "onshore",
    "S": "offshore",
    "Land": "onshore",
    "Sea": "offshore",
    "ON": "onshore",
    "OFF": "offshore",
    "onshore": "onshore",
    "offshore": "offshore",
}
_ENV_TO_REGION: dict[str, str] = {
    "onshore": "Onshore",
    "offshore": "Dutch North Sea",
}

_FLUID_MAP: dict[str, str] = {
    "gas": "gas",
    "olie": "oil",
    "oil": "oil",
    "olie en gas": "oil_and_gas",
    "oil and gas": "oil_and_gas",
}

_FIELDS_LAYER = "nlog:gdw_ng_field_utm"
_FIELDS_PROPS = (
    "FIELD_CODE,FIELD_NAME,HYDROCARBON_MINERAL,STATUS_DESCRIPTION,"
    "OPERATOR,PRODUCTION_START,LANDSEA,DISCOVERY_YEAR,GEOMETRY"
)

_FACILITIES_LAYER = "nlog:GDW_NG_FACILITY_UTM"
_FACILITIES_PROPS = (
    "FACILITY_CODE,FACILITY_NAME,FACILITY_TYPE_CODE,FACILITY_TYPE_DESCRIPTION,"
    "OPERATOR,STATUS_CODE,STATUS_DESCRIPTION,GEOMETRY"
)
_EXCLUDED_FACILITY_TYPES = frozenset({"GTI"})

_WELLS_LAYER = "nlog:gdw_ng_wll_all_utm"
_WELLS_PROPS = (
    "BOREHOLE_CODE,BOREHOLE_NAME,WELL_NAME,BOREHOLE_OR_SIDETRACK_CODE,"
    "BOREHOLE_TYPE_CODE,STATUS,CURRENT_OWNER,ORIGINAL_OPERATOR,"
    "ON_OFFSHORE_CODE,FIELD_NAME,START_DATE_DRILLING,END_DATE_DRILLING,GEOMETRY"
)
# Keep hydrocarbon, gas/CO2 storage, injection, and observation boreholes;
# exclude geothermal (EXP-W, DEV-PH), rock salt (EXP-S, DEV-PS), coal, and geological.
_HYDROCARBON_BOREHOLE_TYPES = frozenset(
    {"DEV-HC", "EXP-HC", "EVA-HC", "EXP", "DEV-I", "DEV-SG", "DEV-SC", "OBS"}
)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _normalize_environment_filter(environment: str | None) -> tuple[str, ...]:
    if environment is None:
        return ("onshore", "offshore")
    norm = environment.strip().lower()
    if norm not in _ENV_TO_LOC:
        raise ValueError(
            f"netherlands environment must be 'onshore' or 'offshore', got {environment!r}"
        )
    return (norm,)


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


def _remote_for_range(y0: int, y1: int, env: str) -> base.RemoteFile:
    loc = _ENV_TO_LOC[env]
    query = urlencode({"yearStart": y0, "yearEnd": y1, "location": loc})
    span = f"{y0}" if y0 == y1 else f"{y0}_{y1}"
    return base.RemoteFile(
        url=f"{_PROD_WELL_URL}?{query}",
        key=f"netherlands_{span}_{env}.json",
        year_min=y0,
        year_max=y1,
    )


def _is_retryable(err: httpx2.HTTPError) -> bool:
    if isinstance(err, httpx2.TransportError):
        return True
    if isinstance(err, httpx2.HTTPStatusError):
        return err.response.status_code in _RETRYABLE_STATUS
    return False


def _post_json_with_retry(
    client: httpx2.Client,
    url: str,
    payload: dict[str, Any],
    *,
    retries: int = 3,
    backoff_s: float = 1.0,
) -> list[dict[str, Any]]:
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError(f"expected JSON list from NLOG API, got {type(data).__name__}")
            return data
        except httpx2.HTTPError as err:
            if attempt + 1 >= attempts or not _is_retryable(err):
                raise
            if backoff_s > 0:
                time.sleep(backoff_s * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_remote_params(url: str) -> tuple[int, int, str]:
    qs = parse_qs(urlparse(url).query)
    try:
        y0 = int(qs["yearStart"][0])
        y1 = int(qs["yearEnd"][0])
        loc = qs["location"][0]
    except (KeyError, IndexError, ValueError) as err:
        raise ValueError(f"invalid NLOG production query URL: {url!r}") from err
    if loc not in ("L", "S"):
        raise ValueError(f"invalid NLOG location code {loc!r} in {url!r}")
    return y0, y1, loc


def _wfs_url(layer: str, properties: str) -> str:
    params = urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer,
            "propertyName": properties,
            "outputFormat": "application/json",
        }
    )
    return f"{_WFS_URL}?{params}"


def _load_bundle(raw: base.RawFile) -> dict[str, Any]:
    if isinstance(raw, (str, Path)):
        text = Path(raw).read_text(encoding="utf-8")
    else:
        data = raw.read()
        text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
    parsed = json.loads(text)
    if not isinstance(parsed, dict) or "streams" not in parsed:
        raise ValueError("not an NLOG Netherlands production bundle; missing 'streams' key")
    return parsed


def _parse_year_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) == 4 and text.isdigit():
        return int(text)
    return None


def _unpivot_stream(
    rows: list[dict[str, Any]],
    rate_col: str,
    unit_kind: str,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    scale = units.BBL_PER_M3 if unit_kind == "liquid" else 1.0
    for row in rows:
        well_raw = str(row.get("wellNm") or "").strip()
        entity_id = schema.normalize_key(well_raw)
        year_int = _parse_year_int(row.get("year"))
        if not entity_id or year_int is None:
            continue
        field_raw = str(row.get("fieldNm") or "").strip()
        field_id = schema.normalize_key(field_raw)
        operator = str(row.get("operatorNm") or "").strip() or None
        for m_key, m_num in _MONTH_KEYS:
            val = row.get(m_key)
            if val is None:
                continue
            try:
                num = float(val)
            except TypeError, ValueError:
                continue
            if np.isnan(num):
                continue
            days = units.days_in_month(year_int, m_num)
            records.append(
                {
                    "entity_id": entity_id,
                    "entity_name": well_raw,
                    "field_id": field_id,
                    "field_name": field_raw or None,
                    "operator": operator,
                    "period": f"{year_int:04d}-{m_num:02d}-01",
                    rate_col: (num * scale) / days,
                }
            )
    return records


def _merge_stream_records(bundle: dict[str, Any]) -> pd.DataFrame:
    streams = bundle.get("streams")
    if not isinstance(streams, dict):
        raise ValueError("NLOG production bundle 'streams' must be a mapping")

    frames: list[pd.DataFrame] = []
    for rate_col, _prod, _ptype, unit_kind in _STREAMS:
        raw_rows = streams.get(rate_col) or []
        if not raw_rows:
            continue
        recs = _unpivot_stream(raw_rows, rate_col, unit_kind)
        if recs:
            frames.append(pd.DataFrame(recs))

    if not frames:
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    present_rates = [col for col, _, _, _ in _STREAMS if col in combined.columns]
    agg: dict[str, Any] = {
        "entity_name": "first",
        "field_name": "first",
        "operator": "first",
    }
    for col in present_rates:
        agg[col] = lambda s: np.nan if s.isna().all() else float(s.sum())

    grouped = combined.groupby(["entity_id", "field_id", "period"], as_index=False, sort=False).agg(
        agg
    )
    # Drop all-zero / all-null well-months (NLOG emits 12 zero months for inactive wells).
    active = grouped[present_rates].fillna(0.0).gt(0.0).any(axis=1)
    return grouped.loc[active].reset_index(drop=True)


def _clean_text(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.mask(text == "", pd.NA)


def _format_discovery_year(series: pd.Series) -> pd.Series:
    cleaned = _clean_text(series)
    return cleaned.map(
        lambda v: f"{v}-01-01" if pd.notna(v) and len(str(v)) == 4 and str(v).isdigit() else pd.NA
    ).astype("string")


def _map_fluid(series: pd.Series) -> pd.Series:
    cleaned = _clean_text(series)
    return cleaned.map(
        lambda v: _FLUID_MAP.get(str(v).lower(), str(v).lower()) if pd.notna(v) else pd.NA
    ).astype("string")


def _map_env_and_region(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    cleaned = _clean_text(series)
    env = cleaned.map(lambda v: _LOC_TO_ENV.get(str(v), pd.NA) if pd.notna(v) else pd.NA).astype(
        "string"
    )
    region = env.map(lambda v: _ENV_TO_REGION.get(str(v), pd.NA) if pd.notna(v) else pd.NA).astype(
        "string"
    )
    return env, region


def _read_geo_inputs(paths: list[Path]) -> gpd.GeoDataFrame:
    if not paths:
        raise ValueError("at least one spatial input path is required")
    frames = [gpd.read_file(path) for path in paths]
    non_empty = [f for f in frames if len(f) > 0]
    if not non_empty:
        return gpd.GeoDataFrame(geometry=gpd.GeoSeries([], crs=_NLOG_CRS), crs=_NLOG_CRS)
    combined = pd.concat(non_empty, ignore_index=True)
    crs = non_empty[0].crs or _NLOG_CRS
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=crs)


def _empty_geo(kind: schema.GeoKind) -> gpd.GeoDataFrame:
    cols = list(schema.GEO_SPECS[kind].columns)
    return geo.prepare_geo(
        gpd.GeoDataFrame(columns=cols, geometry="geometry", crs=_NLOG_CRS),
        kind,
        crs=_NLOG_CRS,
    )


def _parse_fields_geo(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if len(gdf) == 0:
        return _empty_geo(schema.GeoKind.FIELDS)

    work = gdf.copy()
    if work.crs is None:
        work = work.set_crs(_NLOG_CRS)

    field_name = _clean_text(work["FIELD_NAME"])
    work["field_id"] = field_name.map(schema.normalize_key).astype("string")
    work = work.loc[work["field_id"].notna() & (work["field_id"] != "")].copy()
    if len(work) == 0:
        return _empty_geo(schema.GeoKind.FIELDS)

    invalid = work.geometry.notna() & ~work.geometry.is_valid
    if invalid.any():
        work.loc[invalid, "geometry"] = work.loc[invalid, "geometry"].make_valid()

    env, region = _map_env_and_region(work["LANDSEA"])
    work["source"] = "netherlands"
    work["field_code"] = _clean_text(work["FIELD_CODE"])
    work["field_name"] = _clean_text(work["FIELD_NAME"])
    work["region"] = region
    work["environment"] = env
    work["operator"] = _clean_text(work["OPERATOR"])
    work["status"] = _clean_text(work["STATUS_DESCRIPTION"])
    work["primary_fluid"] = _map_fluid(work["HYDROCARBON_MINERAL"])
    work["discovery_date"] = _format_discovery_year(work["DISCOVERY_YEAR"])
    work["production_start_date"] = _clean_text(work["PRODUCTION_START"])

    keep_cols = [
        "source",
        "field_id",
        "field_code",
        "field_name",
        "region",
        "environment",
        "operator",
        "status",
        "primary_fluid",
        "discovery_date",
        "production_start_date",
        "geometry",
    ]
    dissolved = work[keep_cols].dissolve(by="field_id", as_index=False, aggfunc="first")
    # Compute planar area in ED50 / UTM 31N (metres) before reprojecting to EPSG:4326.
    metric = dissolved.to_crs(_NLOG_CRS) if dissolved.crs.to_epsg() != 23031 else dissolved
    dissolved["area_km2"] = (metric.geometry.area / 1_000_000.0).astype("float32")
    return geo.prepare_geo(dissolved, schema.GeoKind.FIELDS, crs=_NLOG_CRS)


def _parse_facilities_geo(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if len(gdf) == 0:
        return _empty_geo(schema.GeoKind.FACILITIES)

    work = gdf.copy()
    if "FACILITY_TYPE_CODE" in work.columns:
        type_code = _clean_text(work["FACILITY_TYPE_CODE"]).str.upper()
        work = work.loc[~type_code.isin(_EXCLUDED_FACILITY_TYPES)].copy()

    fac_code = _clean_text(work["FACILITY_CODE"])
    fac_name = _clean_text(work["FACILITY_NAME"])
    key_src = fac_code.fillna(fac_name)
    work["facility_id"] = key_src.map(schema.normalize_key).astype("string")
    work = work.loc[work["facility_id"].notna() & (work["facility_id"] != "")].copy()
    work = work.drop_duplicates(subset=["facility_id"], keep="first").reset_index(drop=True)

    out = gpd.GeoDataFrame(
        {
            "source": "netherlands",
            "facility_id": work["facility_id"],
            "facility_code": _clean_text(work["FACILITY_CODE"]),
            "facility_name": _clean_text(work["FACILITY_NAME"]),
            "facility_type": _clean_text(work["FACILITY_TYPE_DESCRIPTION"]),
            "operator": _clean_text(work["OPERATOR"]),
            "status": _clean_text(work["STATUS_DESCRIPTION"]),
        },
        geometry=work.geometry,
        crs=work.crs or _NLOG_CRS,
    )
    return geo.prepare_geo(out, schema.GeoKind.FACILITIES, crs=_NLOG_CRS)


def _parse_wells_geo(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    if len(gdf) == 0:
        return _empty_geo(schema.GeoKind.WELLS)

    work = gdf.copy()
    if "BOREHOLE_TYPE_CODE" in work.columns:
        btype = _clean_text(work["BOREHOLE_TYPE_CODE"]).str.upper()
        work = work.loc[btype.isin(_HYDROCARBON_BOREHOLE_TYPES)].copy()

    well_name = _clean_text(work["WELL_NAME"]).fillna(_clean_text(work["BOREHOLE_NAME"]))
    work["well_id"] = well_name.map(schema.normalize_key).astype("string")
    work["well_name"] = well_name
    work = work.loc[work["well_id"].notna() & (work["well_id"] != "")].copy()

    # Prefer the main borehole (BRH) over sidetracks when multiple boreholes share a WELL_NAME.
    is_sidetrack = _clean_text(work["BOREHOLE_OR_SIDETRACK_CODE"]).fillna("") != "BRH"
    work = work.assign(
        _rank=is_sidetrack.astype(int),
        _spud=_clean_text(work["START_DATE_DRILLING"]).fillna("9999-99-99"),
    )
    work = work.sort_values(["well_id", "_rank", "_spud"], kind="mergesort")
    work = work.drop_duplicates(subset=["well_id"], keep="first").reset_index(drop=True)

    env, region = _map_env_and_region(work["ON_OFFSHORE_CODE"])
    field_name = _clean_text(work["FIELD_NAME"])
    operator = _clean_text(work["CURRENT_OWNER"]).fillna(_clean_text(work["ORIGINAL_OPERATOR"]))

    out = gpd.GeoDataFrame(
        {
            "source": "netherlands",
            "well_id": work["well_id"],
            "well_name": work["well_name"],
            "field_id": field_name.map(schema.normalize_key).astype("string"),
            "field_name": field_name,
            "region": region,
            "environment": env,
            "operator": operator,
            "status": _clean_text(work["STATUS"]),
            "spud_date": _clean_text(work["START_DATE_DRILLING"]),
            "completion_date": _clean_text(work["END_DATE_DRILLING"]),
        },
        geometry=work.geometry,
        crs=work.crs or _NLOG_CRS,
    )
    return geo.prepare_geo(out, schema.GeoKind.WELLS, crs=_NLOG_CRS)


@base.register
class Netherlands(base.Source):
    """Netherlands NLOG (TNO / Geological Survey of the Netherlands) connector."""

    name = "netherlands"
    country = "Netherlands"
    country_code = "NL"
    publisher = "NLOG (TNO)"
    entity_type = schema.EntityType.WELL
    docs_url = _DOCS_URL
    licence = (
        "NLOG Open Subsurface Data (TNO / Ministry of Climate Policy and Green Growth)"
        " — free reuse and redistribution with attribution to NLOG.NL."
    )
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
        """List NLOG well-level production query bundles by year range and environment."""
        _ = client
        envs = _normalize_environment_filter(environment)
        current_year = datetime.now(tz=UTC).year
        ranges = _year_ranges(year=year, recent=recent, current_year=current_year)
        return [_remote_for_range(y0, y1, env) for y0, y1 in ranges for env in envs]

    def fetch_production(
        self,
        remote: base.RemoteFile,
        dest: Path,
        *,
        client: httpx2.Client,
    ) -> base.DownloadResult:
        """Fetch all six NLOG production/injection streams for one remote bundle into ``dest``."""
        y0, y1, loc = _parse_remote_params(remote.url)
        env = _LOC_TO_ENV[loc]
        streams: dict[str, list[dict[str, Any]]] = {}
        for rate_col, product, production_type, _unit_kind in _STREAMS:
            payload = {
                "yearStart": y0,
                "yearEnd": y1,
                "product": product,
                "production": production_type,
                "location": loc,
            }
            streams[rate_col] = _post_json_with_retry(client, _PROD_WELL_URL, payload)

        bundle = {
            "source": self.name,
            "year_start": y0,
            "year_end": y1,
            "environment": env,
            "streams": streams,
        }
        raw_bytes = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(f"{dest.name}.part")
        part.write_bytes(raw_bytes)
        part.replace(dest)
        return base.DownloadResult(
            path=dest,
            url=remote.url,
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
            size_bytes=len(raw_bytes),
        )

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        """Parse an NLOG production JSON bundle into canonical daily rates."""
        bundle = _load_bundle(raw)
        env_raw = str(bundle.get("environment") or "").strip()
        env = _LOC_TO_ENV.get(env_raw)
        if env is None:
            raise ValueError(f"invalid environment in NLOG bundle: {env_raw!r}")

        merged = _merge_stream_records(bundle)
        if merged.empty:
            return schema.empty_production()

        n = len(merged)
        rates = {col: merged[col] for col, _, _, _ in _STREAMS if col in merged.columns}
        return base.build_production_table(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=merged["entity_id"],
            entity_name=merged["entity_name"],
            period=merged["period"],
            rates=rates,
            field_id=merged["field_id"],
            field_name=merged["field_name"],
            region=pd.Series([_ENV_TO_REGION[env]] * n, dtype="string"),
            environment=pd.Series([env] * n, dtype="string"),
            operator=merged["operator"],
        )

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Download ED50 / UTM 31N GeoJSON layers from the NLOG OGC WFS."""
        with base.open_client(client) as http:
            if kind is schema.GeoKind.FIELDS:
                url = _wfs_url(_FIELDS_LAYER, _FIELDS_PROPS)
                return [base.download_file(http, url, dest_dir / "netherlands_fields.geojson")]
            if kind is schema.GeoKind.FACILITIES:
                url = _wfs_url(_FACILITIES_LAYER, _FACILITIES_PROPS)
                return [base.download_file(http, url, dest_dir / "netherlands_facilities.geojson")]
            if kind is schema.GeoKind.WELLS:
                url = _wfs_url(_WELLS_LAYER, _WELLS_PROPS)
                return [base.download_file(http, url, dest_dir / "netherlands_wells.geojson")]
        raise ValueError(f"unsupported geo kind: {kind!r}")  # pragma: no cover

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse NLOG WFS GeoJSON layers into canonical EPSG:4326 GeoDataFrames."""
        gdf = _read_geo_inputs(paths)
        if kind is schema.GeoKind.FIELDS:
            return _parse_fields_geo(gdf)
        if kind is schema.GeoKind.FACILITIES:
            return _parse_facilities_geo(gdf)
        if kind is schema.GeoKind.WELLS:
            return _parse_wells_geo(gdf)
        raise ValueError(f"unsupported geo kind: {kind!r}")  # pragma: no cover
