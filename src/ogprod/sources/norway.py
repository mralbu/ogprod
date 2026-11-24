"""Norway - Sodir (Norwegian Offshore Directorate) field production and spatial layers.

Sodir publishes monthly saleable production per field back to 1971 via FactPages
(``field_production_monthly`` CSV) and complete spatial registries via FactMaps'
ArcGIS DataService (requesting only needed columns via ``outFields``):

- Production (1971-present): oil, condensate, NGL, produced water (``Mill Sm3`` -> ``bbl/d``),
  and gas (``Bill Sm3`` -> ``e3m3/d``).
- Fields (layer 7100): field ring-fence polygons, dissolved by ``fldNpdidField``.
- Facilities (layer 6000): fixed platforms, FPSOs, subsea templates, water depth, startup year.
- Wells (layer 5000, paginated): wellbore coordinates, field link, status, spud/completion dates.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx2
import pandas as pd
import pyarrow as pa

from ogprod import geo, schema, units
from ogprod.sources import base

_DOCS_URL = "https://factpages.sodir.no/"

_PROD_CSV_URL = (
    "https://factpages.sodir.no/public?/Factpages/external/tableview/field_production_monthly"
    "&rs:Command=Render&rc:Toolbar=false&rc:Parameters=f&rs:Format=CSV&Top100=false"
    "&IpAddress=1.2.3.4&CultureCode=en"
)

_FACTMAPS_BASE = "https://factmaps.sodir.no/api/rest/services/DataService/Data/MapServer"

_LAYER_CONFIG: dict[schema.GeoKind, tuple[int, str]] = {
    schema.GeoKind.FIELDS: (
        7100,
        "fldNpdidField,fldName,fldMainArea,cmpLongName,"
        "fldCurrentActivitySatus,fldHcType,fldDiscoveryYear",
    ),
    schema.GeoKind.FACILITIES: (
        6000,
        "fclNpdidFacility,fclName,fclKind,fclPhase,"
        "fclBelongsToName,fclCurrentOperatorName,fclWaterDepth,fclStartupDate",
    ),
    schema.GeoKind.WELLS: (
        5000,
        "wlbNpdidWellbore,wlbWellboreName,fldNpdidField,wlbField,"
        "wlbMainArea,wlbDrillingOperator,wlbStatus,wlbEntryDate,wlbCompletionDate",
    ),
}

_REQUIRED_PROD = ("prfInformationCarrier", "prfYear", "prfMonth")
_PAGE_SIZE = 2000


def _clean_int_id(series: pd.Series) -> pd.Series:
    """Normalize numeric NPDIDs (which may arrive float-typed) to clean integer strings."""
    num = pd.to_numeric(series, errors="coerce")
    return num.map(lambda v: str(int(v)) if pd.notna(v) else pd.NA).astype("string")


def _epoch_ms_to_iso(series: pd.Series) -> pd.Series:
    """Convert ArcGIS epoch-millisecond timestamps to ISO ``YYYY-MM-DD`` strings."""
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


def _fetch_arcgis_geojson(
    http: httpx2.Client,
    layer_id: int,
    out_fields: str,
    dest: Path,
) -> base.DownloadResult:
    """Fetch all features of a FactMaps layer (paginating with resultOffset) as one GeoJSON."""
    features: list[dict[str, Any]] = []
    offset = 0
    first_url = ""
    while True:
        url = (
            f"{_FACTMAPS_BASE}/{layer_id}/query"
            f"?where=1%3D1&outFields={out_fields}&outSR=4326"
            f"&resultOffset={offset}&resultRecordCount={_PAGE_SIZE}&f=geojson"
        )
        if not first_url:
            first_url = url
        resp = base.get_with_retry(http, url)
        payload = resp.json()
        page_feats = payload.get("features", [])
        features.extend(page_feats)
        if not _has_more_arcgis_pages(payload, len(page_feats)):
            break
        offset += len(page_feats)

    merged = {"type": "FeatureCollection", "features": features}
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw_bytes = json.dumps(merged).encode("utf-8")
    tmp = dest.with_name(f"{dest.name}.part")
    tmp.write_bytes(raw_bytes)
    tmp.replace(dest)
    import hashlib

    return base.DownloadResult(
        path=dest,
        url=first_url,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
        size_bytes=len(raw_bytes),
    )


def _normalize_main_area(series: pd.Series) -> pd.Series:
    """Normalize Sodir sea-area casing (``'North sea'`` / ``'NORTH SEA'`` -> ``'North Sea'``)."""
    cleaned = series.astype("string").str.strip()
    return cleaned.mask(cleaned == "", pd.NA).str.title()


def _parse_fields_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    cols = {c.lower(): c for c in gdf.columns}
    if "fldnpdidfield" not in cols or "fldname" not in cols:
        raise ValueError(f"not a Sodir FactMaps field GeoJSON; columns: {sorted(gdf.columns)}")

    gdf = gdf.copy()
    gdf["_fid"] = _clean_int_id(gdf[cols["fldnpdidfield"]])
    gdf = gdf.loc[gdf["_fid"].notna()]

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
        dissolved = gpd.GeoDataFrame(combined, geometry="geometry", crs=gdf.crs or schema.GEO_CRS)

    disc = (
        pd.to_numeric(dissolved[cols["flddiscoveryyear"]], errors="coerce").map(
            lambda v: f"{int(v)}-01-01" if pd.notna(v) else pd.NA
        )
        if "flddiscoveryyear" in cols
        else pd.NA
    )
    out = gpd.GeoDataFrame(
        {
            "source": "norway",
            "field_id": dissolved["_fid"],
            "field_code": dissolved["_fid"],
            "field_name": dissolved[cols["fldname"]].astype("string"),
            "basin": pd.NA,
            "region": (
                _normalize_main_area(dissolved[cols["fldmainarea"]])
                if "fldmainarea" in cols
                else pd.NA
            ),
            "environment": "offshore",
            "operator": dissolved[cols["cmplongname"]] if "cmplongname" in cols else pd.NA,
            "status": (
                dissolved[cols["fldcurrentactivitysatus"]]
                if "fldcurrentactivitysatus" in cols
                else pd.NA
            ),
            "primary_fluid": dissolved[cols["fldhctype"]] if "fldhctype" in cols else pd.NA,
            "discovery_date": disc,
        },
        geometry=dissolved.geometry,
        crs=dissolved.crs or schema.GEO_CRS,
    )
    return geo.prepare_geo(out, schema.GeoKind.FIELDS)


def _parse_facilities_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    cols = {c.lower(): c for c in gdf.columns}
    if "fclnpdidfacility" not in cols or "fclname" not in cols:
        raise ValueError(f"not a Sodir FactMaps facility GeoJSON; columns: {sorted(gdf.columns)}")

    fid = _clean_int_id(gdf[cols["fclnpdidfacility"]])
    out = gpd.GeoDataFrame(
        {
            "source": "norway",
            "facility_id": fid,
            "facility_code": fid,
            "facility_name": gdf[cols["fclname"]].astype("string"),
            "facility_type": gdf[cols["fclkind"]] if "fclkind" in cols else pd.NA,
            "fields": gdf[cols["fclbelongstoname"]] if "fclbelongstoname" in cols else pd.NA,
            "basin": pd.NA,
            "operator": (
                gdf[cols["fclcurrentoperatorname"]] if "fclcurrentoperatorname" in cols else pd.NA
            ),
            "status": gdf[cols["fclphase"]] if "fclphase" in cols else pd.NA,
            "start_year": (
                _epoch_ms_to_year(gdf[cols["fclstartupdate"]])
                if "fclstartupdate" in cols
                else pd.NA
            ),
            "water_depth_m": gdf[cols["fclwaterdepth"]] if "fclwaterdepth" in cols else pd.NA,
        },
        geometry=gdf.geometry,
        crs=gdf.crs or schema.GEO_CRS,
    )
    out = out.loc[out["facility_id"].notna()].drop_duplicates(subset=["facility_id"], keep="first")
    return geo.prepare_geo(out, schema.GeoKind.FACILITIES)


def _parse_wells_geojson(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    cols = {c.lower(): c for c in gdf.columns}
    if "wlbnpdidwellbore" not in cols or "wlbwellborename" not in cols:
        raise ValueError(f"not a Sodir FactMaps wellbore GeoJSON; columns: {sorted(gdf.columns)}")

    wid = _clean_int_id(gdf[cols["wlbnpdidwellbore"]])
    out = gpd.GeoDataFrame(
        {
            "source": "norway",
            "well_id": wid,
            "well_name": gdf[cols["wlbwellborename"]].astype("string"),
            "field_id": _clean_int_id(gdf[cols["fldnpdidfield"]])
            if "fldnpdidfield" in cols
            else pd.NA,
            "field_name": gdf[cols["wlbfield"]] if "wlbfield" in cols else pd.NA,
            "basin": pd.NA,
            "region": (
                _normalize_main_area(gdf[cols["wlbmainarea"]]) if "wlbmainarea" in cols else pd.NA
            ),
            "environment": "offshore",
            "operator": (
                gdf[cols["wlbdrillingoperator"]] if "wlbdrillingoperator" in cols else pd.NA
            ),
            "status": gdf[cols["wlbstatus"]] if "wlbstatus" in cols else pd.NA,
            "spud_date": (
                _epoch_ms_to_iso(gdf[cols["wlbentrydate"]]) if "wlbentrydate" in cols else pd.NA
            ),
            "completion_date": (
                _epoch_ms_to_iso(gdf[cols["wlbcompletiondate"]])
                if "wlbcompletiondate" in cols
                else pd.NA
            ),
        },
        geometry=gdf.geometry,
        crs=gdf.crs or schema.GEO_CRS,
    )
    out = out.loc[out["well_id"].notna()].drop_duplicates(subset=["well_id"], keep="first")
    return geo.prepare_geo(out, schema.GeoKind.WELLS)


@base.register
class Norway(base.Source):
    """Norway Sodir (ex-NPD) FactPages & FactMaps connector."""

    name = "norway"
    country = "Norway"
    country_code = "NO"
    publisher = "Sodir"
    entity_type = schema.EntityType.FIELD
    docs_url = _DOCS_URL

    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[base.RemoteFile]:
        """Return the single FactPages ``field_production_monthly`` CSV."""
        if environment is not None and environment.strip().lower() == "onshore":
            raise ValueError(
                "norway (Sodir) covers Norwegian offshore fields only (no onshore data)"
            )
        del client, year, recent  # single full-history CSV covers all years
        return [
            base.RemoteFile(
                url=_PROD_CSV_URL,
                key="field_production_monthly.csv",
                year_min=1971,
                year_max=datetime.now(tz=UTC).year,
            )
        ]

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        """Parse FactPages ``field_production_monthly`` CSV into daily rates (bbl/d, e3m3/d)."""
        frame = pd.read_csv(raw, encoding="utf-8-sig")
        missing = [c for c in _REQUIRED_PROD if c not in frame.columns]
        if missing:
            raise ValueError(f"not a Sodir field_production_monthly CSV; missing {missing}")

        year_raw = frame["prfYear"]
        month_raw = frame["prfMonth"]
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
        bad_period = non_blank & period.isna()
        if bad_period.any():
            samples = frame.loc[bad_period, ["prfYear", "prfMonth"]].head(5).to_dict("records")
            raise ValueError(
                f"Sodir CSV has {int(bad_period.sum())} unparseable year/month row(s): {samples}"
            )
        days = period.dt.days_in_month.astype("float64")
        mill_sm3_to_bpd = 1_000_000.0 * units.BBL_PER_M3 / days
        bill_sm3_to_e3m3d = 1_000_000.0 / days

        def rate(col: str, scale: pd.Series) -> pd.Series | None:
            if col not in frame.columns:
                return None
            return pd.to_numeric(frame[col], errors="coerce") * scale

        rates = {
            k: v
            for k, v in {
                "oil_prod": rate("prfPrdOilNetMillSm3", mill_sm3_to_bpd),
                "condensate_prod": rate("prfPrdCondensateNetMillSm3", mill_sm3_to_bpd),
                "ngl_prod": rate("prfPrdNGLNetMillSm3", mill_sm3_to_bpd),
                "water_prod": rate("prfPrdProducedWaterInFieldMillSm3", mill_sm3_to_bpd),
                "gas_prod": rate("prfPrdGasNetBillSm3", bill_sm3_to_e3m3d),
            }.items()
            if v is not None
        }

        name = frame["prfInformationCarrier"].astype("string").str.strip()
        entity_id = (
            _clean_int_id(frame["prfNpdidInformationCarrier"])
            if "prfNpdidInformationCarrier" in frame.columns
            else name
        )
        return base.build_production_table(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=entity_id,
            entity_name=name,
            period=period,
            rates=rates,
            field_id=entity_id,
            field_name=name,
            environment=pd.Series(["offshore"] * len(frame), dtype="string"),
        )

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Download projected GeoJSON from FactMaps for ``fields``, ``facilities``, or ``wells``."""
        layer_id, out_fields = _LAYER_CONFIG[kind]
        with base.open_client(client) as http:
            return [
                _fetch_arcgis_geojson(
                    http, layer_id, out_fields, dest_dir / f"{kind.value}.geojson"
                )
            ]

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse FactMaps GeoJSON into a validated GeoParquet-ready GeoDataFrame."""
        if kind is schema.GeoKind.FIELDS:
            return _parse_fields_geojson(paths[0])
        if kind is schema.GeoKind.FACILITIES:
            return _parse_facilities_geojson(paths[0])
        if kind is schema.GeoKind.WELLS:
            return _parse_wells_geojson(paths[0])
        raise ValueError(f"unsupported geo kind: {kind!r}")
