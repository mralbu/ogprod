"""Argentina Secretaría de Energía (Capítulo IV) production & spatial connector.

Production (2006-present, Res. 319/93 Capítulo IV) is published as per-year well
CSVs on the national energy open-data catalog (CKAN via ``datos.gob.ar`` /
``datos.energia.gob.ar``), with oil, water, and water injection in ``m³`` and
gas, gas injection, and CO₂ injection in ``10³ m³``.

Per ADR 0011, Argentina (~85k wells, ~17.6M well-months) defaults to field-level
(``yacimiento``) production in Layer 1 / the Hugging Face mirror and omits
high-volume wells from Layer-2 web artifacts (``web_wells = False``), while
preserving local Python opt-in for well-level parsing via
``parse_production(..., granularity="well")`` or ``parse_well_production(...)``.

Geospatial layers:
- ``geo/fields``: ``Producción de hidrocarburos - Yacimientos`` (SHP / GeoJSON CSV, EPSG:4326)
- ``geo/wells``: ``Capítulo IV - Pozos`` (CSV / SHP, EPSG:4326) with fallback to
  ``Listado de pozos cargados por empresas operadoras`` (CSV)
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import geopandas as gpd
import httpx2
import pandas as pd
import pyarrow as pa
import shapely

from ogprod import geo, schema, units
from ogprod.sources import base

_DOCS_URL = "https://datos.gob.ar/dataset/energia-produccion-petroleo-gas-por-pozo-capitulo-iv"
_LICENCE = "CC BY 4.0 — Secretaría de Energía de la Nación (datos.gob.ar)"
_LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

_CKAN_PACKAGE_SHOW_URL = "https://datos.gob.ar/api/3/action/package_show"
_PROD_DATASET_ID = "produccion-de-petroleo-y-gas-por-pozo"
_FIELDS_DATASET_ID = "produccion-hidrocarburos-yacimientos"

# Match standard yearly Capítulo IV CSVs only, skipping "(DDJJ abiertas y cerradas)"
# lookalikes and summary/unconventional tables.
_YEAR_RESOURCE_RE = re.compile(
    r"Producción de Pozos de Gas y Petróleo\s*[-\u2013]?\s*(?P<year>\d{4})"
)
_WELLS_PRIMARY_NAME = "Capítulo IV - Pozos"
_WELLS_FALLBACK_NAME = "Listado de pozos cargados por empresas operadoras"

# (source_col, canonical_rate_col, volume_to_canonical_unit_factor)
# Liquids: m³ -> bbl/d; gases: 10³ m³ (already e3m3) -> e3m3/d.
_RATE_SPECS: tuple[tuple[str, str, float], ...] = (
    ("prod_pet", "oil_prod", units.BBL_PER_M3),
    ("prod_gas", "gas_prod", 1.0),
    ("prod_agua", "water_prod", units.BBL_PER_M3),
    ("iny_agua", "water_inj", units.BBL_PER_M3),
    ("iny_gas", "gas_inj", 1.0),
    ("iny_co2", "co2_inj", 1.0),
)

_PROD_KEEP_COLS: frozenset[str] = frozenset(
    {
        "anio",
        "mes",
        "idpozo",
        "sigla",
        "prod_pet",
        "prod_gas",
        "prod_agua",
        "iny_agua",
        "iny_gas",
        "iny_co2",
        "empresa",
        "idempresa",
        "idareayacimiento",
        "areayacimiento",
        "yacimiento",
        "areapermisoconcesion",
        "area",
        "cuenca",
        "provincia",
    }
)

_MISSING_SENTINELS: frozenset[str] = frozenset(
    {
        "",
        "SIN YACIMIENTO",
        "SIN CUENCA",
        "SIN PROVINCIA",
        "SIN DATOS",
        "SIN INFORMACION",
        "NO DISPONIBLE",
        "N/A",
        "NULL",
        "NONE",
    }
)

_BASIN_MAP: dict[str, str] = {
    "NEUQUINA": "Neuquina",
    "GOLFO SAN JORGE": "Golfo San Jorge",
    "AUSTRAL": "Austral",
    "AUSTRAL MARINA": "Austral Marina",
    "CUYANA": "Cuyana",
    "NOROESTE": "Noroeste",
    "NIRIHUAU": "Ñirihuau",
    "CANADON ASFALTO": "Cañadón Asfalto",
    "CLAROMECO": "Claromecó",
    "CHACO PARANAENSE": "Chaco-Paranaense",
    "MALVINAS": "Malvinas",
    "COLORADO MARINA": "Colorado Marina",
    "SALADO MARINA": "Salado Marina",
    "ARGENTINA": "Argentina",
}

_REGION_MAP: dict[str, str] = {
    "NEUQUEN": "Neuquén",
    "CHUBUT": "Chubut",
    "SANTA CRUZ": "Santa Cruz",
    "MENDOZA": "Mendoza",
    "RIO NEGRO": "Río Negro",
    "TIERRA DEL FUEGO": "Tierra del Fuego",
    "LA PAMPA": "La Pampa",
    "SALTA": "Salta",
    "FORMOSA": "Formosa",
    "JUJUY": "Jujuy",
    "TUCUMAN": "Tucumán",
    "SAN JUAN": "San Juan",
    "SAN LUIS": "San Luis",
    "BUENOS AIRES": "Buenos Aires",
    "ESTADO NACIONAL": "Estado Nacional",
    "PLATAFORMA CONTINENTAL": "Estado Nacional",
}

_OFFSHORE_REGIONS: frozenset[str] = frozenset(
    {"ESTADO NACIONAL", "PLATAFORMA CONTINENTAL", "MAR ARGENTINO"}
)
_OFFSHORE_BASIN_TOKENS: tuple[str, ...] = ("MARINA", "OFFSHORE", "MALVINAS")
_INT_FLOAT_RE = re.compile(r"^(\d+)\.0+$")


def _norm_header(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.strip().lower()).strip("_")


def _first_col(cols: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    for cand in candidates:
        if cand in cols:
            return cols[cand]
    return None


def _clean_int_id(series: pd.Series) -> pd.Series:
    """Normalize numeric IDs that arrive float-typed (``130589.0``) when blanks exist."""
    text = series.astype("string").str.strip()
    text = text.mask(text.isna() | (text == "") | text.str.lower().isin({"nan", "none"}), pd.NA)
    return text.str.replace(_INT_FLOAT_RE, r"\1", regex=True).astype("string")


def _normalize_mapped_name(series: pd.Series | None, lookup: dict[str, str], n: int) -> pd.Series:
    if series is None:
        return pd.Series([pd.NA] * n, dtype="string")

    def _map_one(val: object) -> Any:
        if val is None or pd.isna(val):
            return pd.NA
        key = schema.normalize_key(str(val))
        if key in _MISSING_SENTINELS:
            return pd.NA
        if key in lookup:
            return lookup[key]
        return str(val).strip().title()

    return series.map(_map_one).astype("string")


def _normalize_basin(series: pd.Series | None, n: int) -> pd.Series:
    return _normalize_mapped_name(series, _BASIN_MAP, n)


def _normalize_region(series: pd.Series | None, n: int) -> pd.Series:
    return _normalize_mapped_name(series, _REGION_MAP, n)


def _infer_environment(basin: pd.Series, region: pd.Series) -> pd.Series:
    def _classify(b: object, r: object) -> Any:
        r_key = "" if (r is None or pd.isna(r)) else schema.normalize_key(str(r))
        b_key = "" if (b is None or pd.isna(b)) else schema.normalize_key(str(b))
        if not r_key and not b_key:
            return pd.NA
        if r_key in _OFFSHORE_REGIONS or any(tok in b_key for tok in _OFFSHORE_BASIN_TOKENS):
            return "offshore"
        return "onshore"

    return pd.Series(
        [_classify(b, r) for b, r in zip(basin, region, strict=True)],
        index=basin.index,
        dtype="string",
    )


def _normalize_field_names(
    frame: pd.DataFrame,
    cols: dict[str, str],
) -> tuple[pd.Series, pd.Series]:
    """Resolve canonical ``(field_id, field_name)`` with concession fallback."""
    n = len(frame)
    primary_col = _first_col(cols, ("areayacimiento", "yacimiento"))
    fallback_col = _first_col(cols, ("areapermisoconcesion", "area"))

    def _clean_pair(col: str | None) -> tuple[pd.Series, pd.Series]:
        if col is None:
            return pd.Series([""] * n, dtype="string"), pd.Series([pd.NA] * n, dtype="string")
        raw = frame[col].astype("string").str.strip()
        keys = raw.map(lambda v: "" if pd.isna(v) else schema.normalize_key(str(v)))
        missing = keys.isin(_MISSING_SENTINELS)
        return keys.mask(missing, "").astype("string"), raw.mask(missing, pd.NA).astype("string")

    fid, fname = _clean_pair(primary_col)
    if fallback_col is not None:
        fb_id, fb_name = _clean_pair(fallback_col)
        use_fb = fid == ""
        fid = fid.mask(use_fb, fb_id)
        fname = fname.mask(use_fb, fb_name)
    return fid, fname


def _parse_iso_date(series: pd.Series | None, n: int) -> pd.Series:
    if series is None:
        return pd.Series([pd.NA] * n, dtype="string")
    cleaned = series.astype("string").str.strip()
    cleaned = cleaned.mask(cleaned == "", pd.NA)
    parsed = pd.to_datetime(cleaned, errors="coerce")
    return parsed.dt.strftime("%Y-%m-%d").astype("string")


def _ckan_resources(client: httpx2.Client, dataset_id: str) -> list[dict[str, Any]]:
    url = str(httpx2.URL(_CKAN_PACKAGE_SHOW_URL, params={"id": dataset_id}))
    payload = base.get_with_retry(client, url).json()
    if not payload.get("success", True):
        raise ValueError(f"CKAN package_show failed for {dataset_id!r}: {payload.get('error')}")
    return list(payload["result"].get("resources") or [])


def _select_yearly_resources(resources: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_year: dict[int, list[dict[str, Any]]] = {}
    for res in resources:
        if str(res.get("format") or "").strip().upper() != "CSV":
            continue
        name = str(res.get("name") or "").strip()
        match = _YEAR_RESOURCE_RE.fullmatch(name)
        if match:
            by_year.setdefault(int(match.group("year")), []).append(res)
    return {
        yr: max(
            items,
            key=lambda r: (str(r.get("created") or ""), str(r.get("last_modified") or "")),
        )
        for yr, items in by_year.items()
    }


def _select_geo_resource(
    resources: list[dict[str, Any]],
    *,
    preferred_formats: tuple[str, ...],
    name_fragments: tuple[str, ...] = (),
) -> dict[str, Any]:
    candidates = resources
    if name_fragments:
        for frag in name_fragments:
            frag_low = frag.lower()
            matched = [r for r in resources if frag_low in str(r.get("name") or "").lower()]
            if matched:
                candidates = matched
                break
    for fmt in preferred_formats:
        fmt_matches = [r for r in candidates if str(r.get("format") or "").strip().upper() == fmt]
        if fmt_matches:
            return max(
                fmt_matches,
                key=lambda r: (str(r.get("last_modified") or ""), str(r.get("created") or "")),
            )
    raise ValueError(
        f"no matching CKAN resource found (formats={preferred_formats}, names={name_fragments})"
    )


def _read_production_csv(raw: base.RawFile) -> pd.DataFrame:
    return pd.read_csv(
        raw,
        encoding="utf-8-sig",
        low_memory=False,
        usecols=lambda c: _norm_header(str(c)) in _PROD_KEEP_COLS,
    )


def _build_period(frame: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    anio = pd.to_numeric(frame[cols["anio"]], errors="coerce")
    mes = pd.to_numeric(frame[cols["mes"]], errors="coerce")
    valid = anio.notna() & mes.notna() & (mes >= 1) & (mes <= 12)
    out = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    if valid.any():
        parts = pd.DataFrame(
            {
                "year": anio.loc[valid].astype("int64"),
                "month": mes.loc[valid].astype("int64"),
                "day": 1,
            }
        )
        out.loc[valid] = pd.to_datetime(parts, errors="coerce")
    return out


def _extract_rates(
    frame: pd.DataFrame, cols: dict[str, str], days: pd.Series
) -> dict[str, pd.Series]:
    rates: dict[str, pd.Series] = {}
    for src_col, dst_col, factor in _RATE_SPECS:
        if src_col not in cols:
            continue
        vol = pd.to_numeric(frame[cols[src_col]], errors="coerce")
        rates[dst_col] = (vol * factor) / days
    return rates


def _sum_preserving_nulls(series: pd.Series) -> float:
    if series.isna().all():
        return float("nan")
    return float(series.sum(min_count=1))


def _first_non_null(series: pd.Series) -> Any:
    non_null = series.dropna()
    return non_null.iloc[0] if len(non_null) > 0 else pd.NA


def _aggregate_rows(
    work: pd.DataFrame, key: list[str], rate_cols: list[str], *, force_group: bool
) -> pd.DataFrame:
    if not force_group and not work.duplicated(subset=key).any():
        return work
    # Sort so dominant producing operator/metadata row wins first-non-null tiebreaks.
    oil = work["oil_prod"].fillna(0.0) if "oil_prod" in work.columns else 0.0
    gas = work["gas_prod"].fillna(0.0) if "gas_prod" in work.columns else 0.0
    ordered = work.assign(_rank=-(oil + gas)).sort_values("_rank", kind="mergesort")
    meta_cols = [c for c in work.columns if c not in key and c not in rate_cols]
    agg: dict[str, Any] = dict.fromkeys(meta_cols, _first_non_null)
    for c in rate_cols:
        agg[c] = _sum_preserving_nulls
    return ordered.groupby(key, as_index=False, sort=False).agg(agg)


def _validate_production_columns(cols: dict[str, str], granularity: str) -> None:
    missing_time = [c for c in ("anio", "mes") if c not in cols]
    has_rate = any(src_col in cols for src_col, _, _ in _RATE_SPECS)
    has_entity = (
        "idpozo" in cols
        if granularity == "well"
        else any(c in cols for c in ("idpozo", "areayacimiento", "yacimiento"))
    )
    if missing_time or not has_rate or not has_entity:
        raise ValueError(
            "not an Argentina Capítulo IV production file; "
            f"missing time={missing_time}, has_rate={has_rate}, has_entity={has_entity}"
        )


def _parse_capitulo_iv(
    raw: base.RawFile, *, granularity: Literal["field", "well"] = "field"
) -> pa.Table:
    if granularity not in {"field", "well"}:
        raise ValueError(f"granularity must be 'field' or 'well', got {granularity!r}")
    frame = _read_production_csv(raw)
    cols = {_norm_header(str(c)): c for c in frame.columns}
    _validate_production_columns(cols, granularity)

    period = _build_period(frame, cols)
    valid = period.notna()
    if granularity == "well":
        ids_all = _clean_int_id(frame[cols["idpozo"]])
        valid = valid & ids_all.notna()
    frame = frame.loc[valid].reset_index(drop=True)
    period = period.loc[valid].reset_index(drop=True)
    if len(frame) == 0:
        return schema.empty_production()

    days = period.dt.days_in_month.astype("float64")
    rates = _extract_rates(frame, cols, days)
    field_id, field_name = _normalize_field_names(frame, cols)
    n = len(frame)
    basin = _normalize_basin(frame[cols["cuenca"]] if "cuenca" in cols else None, n)
    region = _normalize_region(frame[cols["provincia"]] if "provincia" in cols else None, n)
    environment = _infer_environment(basin, region)
    op_col = _first_col(cols, ("empresa", "idempresa"))
    operator = frame[op_col].astype("string").str.strip() if op_col else pd.Series([pd.NA] * n)

    if granularity == "well":
        return _build_well_table(
            frame, cols, period, rates, field_id, field_name, basin, region, environment, operator
        )
    return _build_field_table(
        period, rates, field_id, field_name, basin, region, environment, operator
    )


def _build_field_table(
    period: pd.Series,
    rates: dict[str, pd.Series],
    field_id: pd.Series,
    field_name: pd.Series,
    basin: pd.Series,
    region: pd.Series,
    environment: pd.Series,
    operator: pd.Series,
) -> pa.Table:
    missing_field = field_id == ""
    fid = field_id.mask(missing_field, "UNASSIGNED")
    fname = field_name.mask(missing_field, "Unassigned")
    work = pd.DataFrame(
        {
            "field_id": fid,
            "field_name": fname,
            "basin": basin,
            "region": region,
            "environment": environment,
            "operator": operator,
            "period": period,
            **rates,
        }
    )
    grouped = _aggregate_rows(work, ["field_id", "period"], list(rates), force_group=True)
    return base.build_production_table(
        source="argentina",
        entity_type=schema.EntityType.FIELD,
        entity_id=grouped["field_id"],
        entity_name=grouped["field_name"].fillna(grouped["field_id"]),
        period=grouped["period"],
        rates={k: grouped[k] for k in rates},
        field_id=grouped["field_id"],
        field_name=grouped["field_name"],
        basin=grouped["basin"],
        region=grouped["region"],
        environment=grouped["environment"],
        operator=grouped["operator"],
    )


def _build_well_table(
    frame: pd.DataFrame,
    cols: dict[str, str],
    period: pd.Series,
    rates: dict[str, pd.Series],
    field_id: pd.Series,
    field_name: pd.Series,
    basin: pd.Series,
    region: pd.Series,
    environment: pd.Series,
    operator: pd.Series,
) -> pa.Table:
    entity_id = _clean_int_id(frame[cols["idpozo"]])
    entity_name = (
        frame[cols["sigla"]].astype("string").str.strip().mask(lambda s: s == "", pd.NA)
        if "sigla" in cols
        else pd.Series([pd.NA] * len(frame), dtype="string")
    ).fillna(entity_id)
    work = pd.DataFrame(
        {
            "entity_id": entity_id,
            "entity_name": entity_name,
            "field_id": field_id,
            "field_name": field_name,
            "basin": basin,
            "region": region,
            "environment": environment,
            "operator": operator,
            "period": period,
            **rates,
        }
    )
    grouped = _aggregate_rows(
        work, ["entity_id", "field_id", "period"], list(rates), force_group=False
    )
    return base.build_production_table(
        source="argentina",
        entity_type=schema.EntityType.WELL,
        entity_id=grouped["entity_id"],
        entity_name=grouped["entity_name"].fillna(grouped["entity_id"]),
        period=grouped["period"],
        rates={k: grouped[k] for k in rates},
        field_id=grouped["field_id"],
        field_name=grouped["field_name"],
        basin=grouped["basin"],
        region=grouped["region"],
        environment=grouped["environment"],
        operator=grouped["operator"],
    )


def _parse_single_geojson(val: object) -> shapely.Geometry | None:
    if val is None or pd.isna(val):
        return None
    text = str(val).strip()
    if not text:
        return None
    try:
        return shapely.from_geojson(text)
    except Exception:
        return None


def _parse_single_wkb(val: object) -> shapely.Geometry | None:
    if val is None or pd.isna(val):
        return None
    text = str(val).strip()
    if not text:
        return None
    try:
        return shapely.from_wkb(text)
    except Exception:
        return None


def _points_from_lon_lat(lon_s: pd.Series, lat_s: pd.Series) -> gpd.GeoSeries:
    lon = pd.to_numeric(lon_s, errors="coerce")
    lat = pd.to_numeric(lat_s, errors="coerce")
    valid = (
        lon.notna()
        & lat.notna()
        & (lon.abs() <= 180.0)
        & (lat.abs() <= 90.0)
        & ~((lon == 0.0) & (lat == 0.0))
    )
    geoms = [
        shapely.Point(float(x), float(y)) if ok else None
        for x, y, ok in zip(lon, lat, valid, strict=True)
    ]
    return gpd.GeoSeries(geoms, index=lon_s.index, crs=schema.GEO_CRS)


def _geometry_from_csv(frame: pd.DataFrame, cols: dict[str, str]) -> gpd.GeoSeries:
    if "geojson" in cols:
        return gpd.GeoSeries(
            [_parse_single_geojson(v) for v in frame[cols["geojson"]]],
            index=frame.index,
            crs=schema.GEO_CRS,
        )
    if "geom" in cols:
        return gpd.GeoSeries(
            [_parse_single_wkb(v) for v in frame[cols["geom"]]],
            index=frame.index,
            crs=schema.GEO_CRS,
        )
    if "coordenadax" in cols and "coordenaday" in cols:
        # Upstream prose swaps x/y labels, but actual values are x=longitude, y=latitude.
        return _points_from_lon_lat(frame[cols["coordenadax"]], frame[cols["coordenaday"]])
    raise ValueError(
        f"Argentina geo CSV missing geometry column (geojson/geom/coordenadax): {sorted(cols)}"
    )


def _load_geo_source(path: Path) -> gpd.GeoDataFrame:
    if path.suffix.lower() in {".zip", ".shp", ".geojson", ".gpkg"}:
        return gpd.read_file(path)
    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    cols = {_norm_header(str(c)): c for c in frame.columns}
    geom = _geometry_from_csv(frame, cols)
    return gpd.GeoDataFrame(frame, geometry=geom, crs=schema.GEO_CRS)


def _parse_fields_geo(paths: list[Path]) -> gpd.GeoDataFrame:
    if not paths:
        return schema.empty_geo(schema.GeoKind.FIELDS)
    gdf = _load_geo_source(paths[0])
    cols = {_norm_header(str(c)): c for c in gdf.columns if c != gdf.geometry.name}
    name_col = _first_col(cols, ("yacimiento", "areayacimiento", "nombre_yac", "nombre"))
    if name_col is None:
        raise ValueError(f"not an Argentina yacimientos geo file; columns: {sorted(gdf.columns)}")

    work = gdf.copy()
    work["_fid"] = work[name_col].map(lambda v: "" if pd.isna(v) else schema.normalize_key(str(v)))
    work = work.loc[~work["_fid"].isin(_MISSING_SENTINELS)]
    if len(work) == 0:
        return schema.empty_geo(schema.GeoKind.FIELDS)

    has_geom = work.geometry.notna() & ~work.geometry.is_empty
    with_geom = work.loc[has_geom].copy()
    if len(with_geom) == 0:
        return schema.empty_geo(schema.GeoKind.FIELDS)
    with_geom = with_geom.set_geometry(with_geom.geometry.make_valid())
    dissolved = with_geom.dissolve(by="_fid", aggfunc="first").reset_index()

    n = len(dissolved)
    code_col = _first_col(cols, ("cod_yacimiento", "cod_yacimi", "idareayacimiento", "codigo"))
    op_col = _first_col(cols, ("empresa", "empresa_informante", "empresa_in", "operador"))
    area_col = _first_col(cols, ("superficie", "sup_km2", "area_km2"))
    basin = _normalize_basin(dissolved[cols["cuenca"]] if "cuenca" in cols else None, n)
    region = _normalize_region(dissolved[cols["provincia"]] if "provincia" in cols else None, n)
    out = gpd.GeoDataFrame(
        {
            "source": "argentina",
            "field_id": dissolved["_fid"],
            "field_code": dissolved[code_col] if code_col else pd.NA,
            "field_name": dissolved[name_col].astype("string").str.strip(),
            "basin": basin,
            "region": region,
            "environment": _infer_environment(basin, region),
            "operator": dissolved[op_col] if op_col else pd.NA,
            "area_km2": pd.to_numeric(dissolved[area_col], errors="coerce") if area_col else pd.NA,
        },
        geometry=dissolved.geometry,
        crs=dissolved.crs or schema.GEO_CRS,
    )
    return geo.prepare_geo(out, schema.GeoKind.FIELDS)


def _clean_point_geometries(series: gpd.GeoSeries) -> gpd.GeoSeries:
    def _clean(geom: Any) -> shapely.Geometry | None:
        if geom is None or not isinstance(geom, shapely.Geometry) or geom.is_empty:
            return None
        if geom.geom_type != "Point":
            return None
        if abs(geom.x) > 180.0 or abs(geom.y) > 90.0 or (geom.x == 0.0 and geom.y == 0.0):
            return None
        return geom

    return gpd.GeoSeries([_clean(g) for g in series], index=series.index, crs=schema.GEO_CRS)


def _parse_wells_geo(paths: list[Path]) -> gpd.GeoDataFrame:
    if not paths:
        return schema.empty_geo(schema.GeoKind.WELLS)
    gdf = _load_geo_source(paths[0])
    cols = {_norm_header(str(c)): c for c in gdf.columns if c != gdf.geometry.name}
    if "idpozo" not in cols:
        raise ValueError(f"not an Argentina pozos geo file; missing 'idpozo': {sorted(cols)}")

    well_ids = _clean_int_id(gdf[cols["idpozo"]])
    keep = well_ids.notna()
    gdf = gdf.loc[keep].reset_index(drop=True)
    well_ids = well_ids.loc[keep].reset_index(drop=True)
    if len(gdf) == 0:
        return schema.empty_geo(schema.GeoKind.WELLS)

    n = len(gdf)
    fid, fname = _normalize_field_names(gdf, cols)
    fid = fid.mask(fid == "", pd.NA)
    basin = _normalize_basin(gdf[cols["cuenca"]] if "cuenca" in cols else None, n)
    region = _normalize_region(gdf[cols["provincia"]] if "provincia" in cols else None, n)
    op_col = _first_col(cols, ("empresa", "idempresa"))
    status_col = _first_col(cols, ("tipoestado", "clasificacion"))
    lift_col = _first_col(cols, ("tipoextraccion",))
    spud_col = _first_col(cols, ("adjiv_fecha_inicio_perf", "adjiv_fecha_inicio", "adjiv_fech"))
    comp_col = _first_col(cols, ("adjiv_fecha_fin_term", "adjiv_fecha_fin"))
    sigla = (
        gdf[cols["sigla"]].astype("string").str.strip().mask(lambda s: s == "", pd.NA)
        if "sigla" in cols
        else pd.Series([pd.NA] * n, dtype="string")
    ).fillna(well_ids)

    geoms = _clean_point_geometries(gdf.geometry)
    out = gpd.GeoDataFrame(
        {
            "source": "argentina",
            "well_id": well_ids,
            "well_name": sigla,
            "field_id": fid,
            "field_name": fname,
            "basin": basin,
            "region": region,
            "environment": _infer_environment(basin, region),
            "operator": gdf[op_col] if op_col else pd.NA,
            "status": gdf[status_col] if status_col else pd.NA,
            "lift_method": gdf[lift_col] if lift_col else pd.NA,
            "spud_date": _parse_iso_date(gdf[spud_col] if spud_col else None, n),
            "completion_date": _parse_iso_date(gdf[comp_col] if comp_col else None, n),
        },
        geometry=geoms,
        crs=gdf.crs or schema.GEO_CRS,
    )
    deduped = gpd.GeoDataFrame(
        out.drop_duplicates(subset=["well_id"], keep="last"),
        geometry="geometry",
        crs=out.crs,
    )
    return geo.prepare_geo(deduped, schema.GeoKind.WELLS)


def parse_well_production(raw: base.RawFile) -> pa.Table:
    """Parse an Argentina Capítulo IV CSV at individual well granularity (local Python opt-in)."""
    return _parse_capitulo_iv(raw, granularity="well")


@base.register
class Argentina(base.Source):
    """Argentina Secretaría de Energía (Res. 319/93 Capítulo IV) connector."""

    name = "argentina"
    country = "Argentina"
    country_code = "AR"
    publisher = "Secretaría de Energía"
    entity_type = schema.EntityType.FIELD
    docs_url = _DOCS_URL
    licence = _LICENCE
    licence_url = _LICENCE_URL
    web_wells = False

    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[base.RemoteFile]:
        """Discover per-year Capítulo IV production CSVs from the national CKAN catalog."""
        if environment is not None and environment.strip().lower() not in {"onshore", "offshore"}:
            raise ValueError(
                f"argentina environment must be 'onshore' or 'offshore', got {environment!r}"
            )
        with base.open_client(client) as http:
            resources = _ckan_resources(http, _PROD_DATASET_ID)
        by_year = _select_yearly_resources(resources)
        if not by_year:
            raise ValueError(f"no Capítulo IV yearly CSVs found in CKAN dataset {_PROD_DATASET_ID}")

        current_year = datetime.now(tz=UTC).year
        if year is not None:
            if year not in by_year:
                raise ValueError(
                    f"no Argentina Capítulo IV CSV for {year}; available: {sorted(by_year)}"
                )
            chosen_years = [year]
        elif recent:
            chosen_years = [y for y in sorted(by_year) if y >= current_year - 1]
            if not chosen_years:
                chosen_years = [max(by_year)]
        else:
            chosen_years = sorted(by_year)

        return [
            base.RemoteFile(
                url=str(by_year[y]["url"]),
                key=f"capitulo_iv_{y}.csv",
                year_min=y,
                year_max=y,
            )
            for y in chosen_years
        ]

    def parse_production(
        self,
        raw: base.RawFile,
        *,
        granularity: Literal["field", "well"] = "field",
    ) -> pa.Table:
        """Parse a Capítulo IV CSV into canonical daily rates (default: field rollup per ADR 0011).

        Pass ``granularity="well"`` (or call :func:`parse_well_production`) for local
        well-level analysis in Python.
        """
        return _parse_capitulo_iv(raw, granularity=granularity)

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Download Argentina yacimientos (fields) or Capítulo IV well coordinates."""
        if kind is schema.GeoKind.FACILITIES:
            return super().fetch_geo(kind, dest_dir, client=client)
        with base.open_client(client) as http:
            if kind is schema.GeoKind.FIELDS:
                resources = _ckan_resources(http, _FIELDS_DATASET_ID)
                res = _select_geo_resource(resources, preferred_formats=("SHP", "CSV"))
                ext = ".zip" if str(res.get("format") or "").strip().upper() == "SHP" else ".csv"
                return [base.download_file(http, str(res["url"]), dest_dir / f"yacimientos{ext}")]
            if kind is schema.GeoKind.WELLS:
                resources = _ckan_resources(http, _PROD_DATASET_ID)
                res = _select_geo_resource(
                    resources,
                    preferred_formats=("CSV", "SHP"),
                    name_fragments=(_WELLS_PRIMARY_NAME, _WELLS_FALLBACK_NAME),
                )
                ext = ".zip" if str(res.get("format") or "").strip().upper() == "SHP" else ".csv"
                return [base.download_file(http, str(res["url"]), dest_dir / f"pozos{ext}")]
        return super().fetch_geo(kind, dest_dir, client=client)

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse downloaded Argentina yacimientos polygons or well point layers."""
        if kind is schema.GeoKind.FIELDS:
            return _parse_fields_geo(paths)
        if kind is schema.GeoKind.WELLS:
            return _parse_wells_geo(paths)
        return super().parse_geo(kind, paths)
