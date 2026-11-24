"""Mexico - SENER / CNIH (ex-CNH) SIH production and Mapa Hidrocarburos spatial layers.

Production data comes from SENER's Sistema de Información de Hidrocarburos (SIH)
bulk downloads at ``sih-hidrocarburos.energia.gob.mx/downloads/``:

- ``PRODUCCION_CAMPOS.csv`` (1960-present, ~18 MB): monthly field-level daily rates
  for oil, condensate, water (``Mbd`` = thousand bbl/d -> ``bbl/d``) and
  associated + non-associated gas (``MMpcd`` = million scf/d -> ``e3m3/d``).
- ``PRODUCCION_POZOS.zip`` / ``POZOS_COMPILADO.csv`` (1930-present): monthly
  well-level daily rates (auto-detected by ``parse_production`` on CSV or ZIP).

Spatial data comes from SENER's Mapa de Hidrocarburos public shapefile export
service (``mapa-hidrocarburos.energia.gob.mx``):

- Fields: ``cmp_cmp_rsv`` polygons (477 fields, ``EPSG:4326``), enriched with
  ``basin`` from ``geo_petro_prov`` (Provincias Petroleras).
- Facilities: ``v_iftep_instalaciones`` points (1,363 platforms/stations).
- Wells: 6 regional zones (``v_zon_pozos_*`` points).
"""

from __future__ import annotations

import io
import json
import re
import unicodedata
import zipfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa

from ogprod import geo, schema, units
from ogprod.sources import base

_DOCS_URL = "https://sih-hidrocarburos.energia.gob.mx/"
_SIH_DOWNLOADS = "https://sih-hidrocarburos.energia.gob.mx/downloads/"
_CAMPOS_PROD_URL = _SIH_DOWNLOADS + "PRODUCCION_CAMPOS.csv"
_POZOS_PROD_URL = _SIH_DOWNLOADS + "PRODUCCION_POZOS.zip"

_MAP_BASE = "https://mapa-hidrocarburos.energia.gob.mx"
_EXPORT_URL = f"{_MAP_BASE}/php/ws/export.php"
_GET_FILE_URL = f"{_MAP_BASE}/php/tools/getFile.php"
_PETRO_PROV_LAYER = "geo_petro_prov"
# INEGI Mexico ITRF2008 / Lambert Conformal Conic for metric polygon overlap areas.
_EQUAL_AREA_CRS = "EPSG:6372"

_WELL_ZONE_LAYERS: tuple[str, ...] = (
    "v_zon_pozos_burgos",
    "v_zon_pozos_tampico",
    "v_zon_pozos_ver",
    "v_zon_pozos_cuenca_sur",
    "v_zon_pozos_agu_som",
    "v_zon_pozos_agu_pro",
)

_MMPCD_TO_E3M3D: float = units.volume_factor("mmcf", "e3m3")

_SPANISH_MONTHS: dict[str, str] = {
    "ENE": "01",
    "FEB": "02",
    "MAR": "03",
    "ABR": "04",
    "MAY": "05",
    "JUN": "06",
    "JUL": "07",
    "AGO": "08",
    "SEP": "09",
    "OCT": "10",
    "NOV": "11",
    "DIC": "12",
}

_SPANISH_LOWER_WORDS = frozenset({"a", "de", "del", "e", "en", "la", "las", "los", "y"})
_HYPHEN_SPACE_RE = re.compile(r"\s*-\s*")

# Restore accents stripped by uppercase ASCII shapefiles/CSVs (keyed by normalize_key).
_BASIN_CANONICAL: dict[str, str] = {
    "CINTURON PLEGADO DE CHIAPAS": "Cinturón Plegado de Chiapas",
    "CINTURON PLEGADO DE LA SIERRA MADRE ORIENTAL": "Cinturón Plegado de la Sierra Madre Oriental",
    "GOLFO DE MEXICO PROFUNDO": "Golfo de México Profundo",
    "PLATAFORMA DE YUCATAN": "Plataforma de Yucatán",
    "VIZCAINO-LA PURISIMA-IRAY": "Vizcaíno-La Purísima-Iray",
}

_REGION_CANONICAL: dict[str, str] = {
    "CIUDAD DE MEXICO": "Ciudad de México",
    "MEXICO": "México",
    "MICHOACAN": "Michoacán",
    "MICHOACAN DE OCAMPO": "Michoacán de Ocampo",
    "NUEVO LEON": "Nuevo León",
    "QUERETARO": "Querétaro",
    "SAN LUIS POTOSI": "San Luis Potosí",
    "YUCATAN": "Yucatán",
    "ZONA ECONOMICA EXCLUSIVA": "Zona Económica Exclusiva",
}


def _norm_header(name: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\s_()]+", "_", ascii_text.strip().lower()).strip("_")


def _spanish_title_case(text: str) -> str:
    collapsed = _HYPHEN_SPACE_RE.sub("-", " ".join(text.split()))
    words = collapsed.split(" ")
    formatted: list[str] = []
    for idx, word in enumerate(words):
        parts = [
            part.lower()
            if idx > 0 and p_idx == 0 and part.lower() in _SPANISH_LOWER_WORDS
            else part.capitalize()
            for p_idx, part in enumerate(word.split("-"))
        ]
        formatted.append("-".join(parts))
    return " ".join(formatted)


def _normalize_spanish_label(value: object, canonical: Mapping[str, str]) -> object:
    if pd.isna(value):
        return pd.NA
    collapsed = _HYPHEN_SPACE_RE.sub("-", str(value).strip())
    key = schema.normalize_key(collapsed)
    if key in _MISSING_FIELD_SENTINELS:
        return pd.NA
    if key in canonical:
        return canonical[key]
    return _spanish_title_case(collapsed)


def _normalize_mexico_basin(series: pd.Series) -> pd.Series:
    return series.map(lambda v: _normalize_spanish_label(v, _BASIN_CANONICAL)).astype("string")


def _normalize_mexico_region(series: pd.Series) -> pd.Series:
    return series.map(lambda v: _normalize_spanish_label(v, _REGION_CANONICAL)).astype("string")


def _normalize_environment(series: pd.Series | None, length: int) -> pd.Series:
    if series is None:
        return pd.Series([pd.NA] * length, dtype="string")

    def map_env(val: object) -> str | object:
        if pd.isna(val):
            return pd.NA
        text = str(val).strip().lower()
        if "terrestre" in text:
            return "onshore"
        if any(k in text for k in ("agua", "mar", "somera", "profunda")):
            return "offshore"
        return pd.NA

    return series.map(map_env).astype("string")


def _parse_spanish_date(series: pd.Series) -> pd.Series:
    """Parse ``DD-MMM-YYYY`` (Spanish 3-letter months, e.g. ``21-ABR-2020``) to ISO date."""

    def convert(val: object) -> object:
        if pd.isna(val):
            return pd.NA
        text = str(val).strip().upper()
        parts = text.split("-")
        if len(parts) == 3 and parts[1] in _SPANISH_MONTHS:
            return f"{parts[2]}-{_SPANISH_MONTHS[parts[1]]}-{parts[0].zfill(2)}"
        parsed = pd.to_datetime(text, errors="coerce")
        return parsed.strftime("%Y-%m-%d") if pd.notna(parsed) else pd.NA

    return series.map(convert).astype("string")


def _read_sih_csv(raw: base.RawFile) -> pd.DataFrame:
    """Read a Mexico SIH CSV (or ZIP containing ``POZOS_COMPILADO.csv``), skipping banner lines."""
    if isinstance(raw, str | Path) and Path(raw).suffix.lower() == ".zip":
        with zipfile.ZipFile(raw) as archive:
            member = next(
                (m for m in archive.namelist() if m.upper().endswith("POZOS_COMPILADO.csv")),
                next((m for m in archive.namelist() if m.lower().endswith(".csv")), None),
            )
            if member is None:
                raise ValueError(f"Mexico SIH zip contains no CSV files: {raw}")
            with archive.open(member) as handle:
                return _read_sih_bytes(handle.read())
    if isinstance(raw, str | Path):
        return _read_sih_bytes(Path(raw).read_bytes())
    raw.seek(0)
    return _read_sih_bytes(raw.read())


def _read_sih_bytes(content: bytes) -> pd.DataFrame:
    text = _decode_sih(content)
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines[:30]) if line.strip().lower().startswith("fecha,")),
        None,
    )
    if header_idx is None:
        raise ValueError("not a Mexico SIH production CSV; no 'FECHA,...' header row found")
    body = "\n".join(lines[header_idx:])
    return pd.read_csv(io.StringIO(body), dtype=str, low_memory=False)


def _decode_sih(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1", errors="replace")


def _sum_gas_mmpcd(frame: pd.DataFrame, cols: dict[str, str], keys: tuple[str, ...]) -> pd.Series:
    present = [pd.to_numeric(frame[cols[k]], errors="coerce") for k in keys if k in cols]
    if not present:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    if len(present) == 1:
        return present[0] * _MMPCD_TO_E3M3D
    a, b = present[0], present[1]
    both_na = a.isna() & b.isna()
    return (a.fillna(0.0) + b.fillna(0.0)).mask(both_na) * _MMPCD_TO_E3M3D


_MISSING_FIELD_SENTINELS = frozenset({"", "NO DISPONIBLE", "N/A", "NA", "-"})


def _normalize_mexico_field_id(value: object) -> str:
    if pd.isna(value):
        return ""
    return schema.normalize_key(str(value).replace("-", " "))


def _check_unparseable_dates(raw: pd.Series, parsed: pd.Series, label: str) -> None:
    bad = raw.notna() & (raw != "") & parsed.isna()
    if bad.any():
        samples = raw.loc[bad].drop_duplicates().head(5).tolist()
        raise ValueError(
            f"Mexico SIH {label} CSV has {int(bad.sum())} unparseable FECHA value(s): {samples}"
        )


def _parse_campos_frame(frame: pd.DataFrame, cols: dict[str, str]) -> pa.Table:
    raw_rows = len(frame)
    fecha_raw = frame[cols["fecha"]].astype("string").str.strip()
    period = pd.to_datetime(fecha_raw, format="%m/%Y", errors="coerce")
    _check_unparseable_dates(fecha_raw, period, "campos")
    valid = period.notna() & frame[cols["campo_sih"]].notna()
    frame = frame.loc[valid].reset_index(drop=True)
    period = period.loc[valid].reset_index(drop=True)
    if len(frame) == 0:
        if raw_rows > 0:
            raise ValueError(
                f"Mexico SIH campos CSV had {raw_rows} row(s) but 0 valid field/period rows"
            )
        return schema.empty_production()

    field_name = (
        frame[cols["campo_oficial"]].astype("string").str.strip()
        if "campo_oficial" in cols
        else frame[cols["campo_sih"]].astype("string").str.strip()
    )
    field_id = field_name.map(_normalize_mexico_field_id)
    fallback_id = frame[cols["campo_sih"]].map(_normalize_mexico_field_id)
    field_id = field_id.mask(field_id == "", fallback_id)

    rates = {
        "oil_prod": pd.to_numeric(frame[cols["petroleo_mbd"]], errors="coerce") * 1000.0
        if "petroleo_mbd" in cols
        else pd.Series(np.nan, index=frame.index),
        "condensate_prod": pd.to_numeric(frame[cols["condensado_mbd"]], errors="coerce") * 1000.0
        if "condensado_mbd" in cols
        else pd.Series(np.nan, index=frame.index),
        "gas_prod": _sum_gas_mmpcd(frame, cols, ("gas_asoc_mmpcd", "gas_nasoc_mmpcd")),
        "water_prod": pd.to_numeric(frame[cols["agua_mbd"]], errors="coerce") * 1000.0
        if "agua_mbd" in cols
        else pd.Series(np.nan, index=frame.index),
    }

    work = pd.DataFrame(
        {
            "field_id": field_id,
            "field_name": field_name,
            "environment": _normalize_environment(
                frame[cols["ubicacion"]] if "ubicacion" in cols else None, len(frame)
            ),
            "period": period,
            **rates,
        }
    )
    work = _aggregate_duplicate_keys(work, ["field_id", "period"], list(rates))
    return base.build_production_table(
        source="mexico",
        entity_type=schema.EntityType.FIELD,
        entity_id=work["field_id"],
        entity_name=work["field_name"],
        period=work["period"],
        rates={k: work[k] for k in rates},
        field_id=work["field_id"],
        field_name=work["field_name"],
        environment=work["environment"],
    )


def _parse_pozos_frame(frame: pd.DataFrame, cols: dict[str, str]) -> pa.Table:
    raw_rows = len(frame)
    fecha_raw = frame[cols["fecha"]].astype("string").str.strip()
    period = pd.to_datetime(fecha_raw, format="%d-%m-%Y", errors="coerce")
    _check_unparseable_dates(fecha_raw, period, "pozos")
    period = period.dt.to_period("M").dt.to_timestamp()
    id_col = cols["identificador"] if "identificador" in cols else cols["nombre_del_pozo"]
    valid = period.notna() & frame[id_col].notna()
    frame = frame.loc[valid].reset_index(drop=True)
    period = period.loc[valid].reset_index(drop=True)
    if len(frame) == 0:
        if raw_rows > 0:
            raise ValueError(
                f"Mexico SIH pozos CSV had {raw_rows} row(s) but 0 valid well/period rows"
            )
        return schema.empty_production()

    entity_id = frame[id_col].astype("string").str.strip()
    entity_name = (
        frame[cols["nombre_del_pozo"]].astype("string").str.strip()
        if "nombre_del_pozo" in cols
        else entity_id
    )
    rates = {
        "oil_prod": pd.to_numeric(frame[cols["petroleo_mbd"]], errors="coerce") * 1000.0
        if "petroleo_mbd" in cols
        else pd.Series(np.nan, index=frame.index),
        "condensate_prod": pd.to_numeric(frame[cols["condensado_mbd"]], errors="coerce") * 1000.0
        if "condensado_mbd" in cols
        else pd.Series(np.nan, index=frame.index),
        "gas_prod": _sum_gas_mmpcd(frame, cols, ("gas_asociado_mmpcd", "gas_no_asociado_mmpcd")),
        "water_prod": pd.to_numeric(frame[cols["agua_mbd"]], errors="coerce") * 1000.0
        if "agua_mbd" in cols
        else pd.Series(np.nan, index=frame.index),
    }
    work = pd.DataFrame(
        {
            "entity_id": entity_id,
            "entity_name": entity_name,
            "basin": _normalize_mexico_basin(frame[cols["cuenca"]]) if "cuenca" in cols else pd.NA,
            "period": period,
            **rates,
        }
    )
    work = _aggregate_duplicate_keys(work, ["entity_id", "period"], list(rates))
    return base.build_production_table(
        source="mexico",
        entity_type=schema.EntityType.WELL,
        entity_id=work["entity_id"],
        entity_name=work["entity_name"],
        period=work["period"],
        rates={k: work[k] for k in rates},
        basin=work["basin"],
    )


def _aggregate_duplicate_keys(
    work: pd.DataFrame, key: list[str], rate_cols: list[str]
) -> pd.DataFrame:
    if not work.duplicated(subset=key).any():
        return work
    meta_cols = [c for c in work.columns if c not in key and c not in rate_cols]
    agg: dict[str, str] = dict.fromkeys(meta_cols, "first")
    for c in rate_cols:
        agg[c] = "sum"
    return work.groupby(key, as_index=False, sort=False).agg(agg)


def _export_map_layer(
    http: httpx2.Client, layer: str, name: str, dest: Path
) -> base.DownloadResult:
    """Request a shapefile ZIP from Mapa de Hidrocarburos and stream it to ``dest``."""
    req_payload = {
        "layers": [{"locale": "es_MX", "layer": layer, "format": "shp", "fileName": name}]
    }
    resp = http.post(_EXPORT_URL, data={"params": json.dumps(req_payload)})
    resp.raise_for_status()
    result = resp.json()
    if "error" in result:
        raise ValueError(f"Mexico Mapa Hidrocarburos export failed for {layer}: {result['error']}")
    file_path = str(result["filePath"]).replace("\\", "/")
    ext = file_path.rsplit(".", 1)[-1].lower()
    dl_params = {
        "fileName": name,
        "fileExtension": ext,
        "filePath": file_path,
        "fileRemove": True,
    }
    url = str(httpx2.URL(_GET_FILE_URL, params={"params": json.dumps(dl_params)}))
    return base.download_file(http, url, dest)


def _assign_mexico_basins(dissolved: gpd.GeoDataFrame, provinces_path: Path) -> pd.Series:
    prov = gpd.read_file(provinces_path)
    prov_cols = {_norm_header(c): c for c in prov.columns}
    if "nombre" not in prov_cols:
        raise ValueError(
            f"not a Mexico petroleum provinces shapefile; columns: {sorted(prov.columns)}"
        )
    assigned = geo.assign_by_largest_overlap(
        dissolved,
        prov,
        id_column="_fid",
        label_column=prov_cols["nombre"],
        equal_area_crs=_EQUAL_AREA_CRS,
    )
    return _normalize_mexico_basin(assigned)


def _split_field_paths(paths: list[Path]) -> tuple[Path, Path | None]:
    if len(paths) == 1:
        return paths[0], None
    prov_path = next((p for p in paths if "provincia" in p.stem.lower()), paths[1])
    fields_path = next((p for p in paths if p != prov_path), paths[0])
    return fields_path, prov_path


def _parse_fields_shp(path: Path, provinces_path: Path | None = None) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    cols = {_norm_header(c): c for c in gdf.columns}
    if "nombre" not in cols:
        raise ValueError(f"not a Mexico fields shapefile; columns: {sorted(gdf.columns)}")

    gdf = gdf.copy()
    gdf["_fid"] = gdf[cols["nombre"]].map(
        lambda v: schema.normalize_key(None if pd.isna(v) else str(v))
    )
    gdf = gdf.loc[gdf["_fid"] != ""]
    has_geom = gdf.geometry.notna() & ~gdf.geometry.is_empty
    with_geom = gdf.loc[has_geom].copy()
    with_geom = with_geom.set_geometry(with_geom.geometry.make_valid())
    dissolved = with_geom.dissolve(by="_fid", aggfunc="first").reset_index()

    basin: pd.Series | object = (
        _assign_mexico_basins(dissolved, provinces_path) if provinces_path is not None else pd.NA
    )
    env_col = next((c for k, c in cols.items() if k.startswith("ubicaci")), None)
    out = gpd.GeoDataFrame(
        {
            "source": "mexico",
            "field_id": dissolved["_fid"],
            "field_name": dissolved[cols["nombre"]].astype("string").str.strip(),
            "basin": basin,
            "environment": _normalize_environment(
                dissolved[env_col] if env_col else None, len(dissolved)
            ),
            "area_km2": pd.to_numeric(dissolved[cols["superficie"]], errors="coerce")
            if "superficie" in cols
            else pd.NA,
        },
        geometry=dissolved.geometry,
        crs=dissolved.crs or schema.GEO_CRS,
    )
    return geo.prepare_geo(out, schema.GeoKind.FIELDS)


def _parse_facilities_shp(path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(path)
    cols = {_norm_header(c): c for c in gdf.columns}
    name_col = next((c for k, c in cols.items() if k.startswith("nombre")), None)
    if name_col is None:
        raise ValueError(f"not a Mexico facilities shapefile; columns: {sorted(gdf.columns)}")

    names = gdf[name_col].astype("string").str.strip()
    fac_ids = names.map(lambda v: schema.normalize_key(None if pd.isna(v) else str(v)))
    start_col = next((c for k, c in cols.items() if k.startswith("fecha_de_c")), None)
    start_year = (
        pd.to_datetime(gdf[start_col], errors="coerce").dt.year.astype("Int32")
        if start_col
        else pd.NA
    )
    status_col = next((c for k, c in cols.items() if k.startswith("estado")), None)
    out = gpd.GeoDataFrame(
        {
            "source": "mexico",
            "facility_id": fac_ids,
            "facility_name": names,
            "facility_type": gdf[cols["tipo"]] if "tipo" in cols else pd.NA,
            "status": gdf[status_col] if status_col else pd.NA,
            "start_year": start_year,
        },
        geometry=gdf.geometry,
        crs=gdf.crs or schema.GEO_CRS,
    )
    out = out.loc[out["facility_id"] != ""].drop_duplicates(subset=["facility_id"], keep="first")
    return geo.prepare_geo(out, schema.GeoKind.FACILITIES)


def _parse_wells_shps(paths: list[Path]) -> gpd.GeoDataFrame:
    frames = [gpd.read_file(p) for p in paths]
    if not frames:
        return schema.empty_geo(schema.GeoKind.WELLS)
    combined = pd.concat(frames, ignore_index=True)
    gdf = gpd.GeoDataFrame(combined, geometry="geometry", crs=frames[0].crs or schema.GEO_CRS)
    cols = {_norm_header(c): c for c in gdf.columns}
    if "pozo" not in cols:
        raise ValueError(f"not a Mexico wells shapefile; columns: {sorted(gdf.columns)}")

    names = gdf[cols["pozo"]].astype("string").str.strip()
    if "campo" in cols:
        raw_field = gdf[cols["campo"]].astype("string").str.strip()
        norm_field = raw_field.map(_normalize_mexico_field_id)
        missing_field = norm_field.isin(_MISSING_FIELD_SENTINELS)
        field_id: pd.Series | object = norm_field.mask(missing_field, pd.NA).astype("string")
        field_name: pd.Series | object = raw_field.mask(missing_field, pd.NA).astype("string")
    else:
        field_id = pd.NA
        field_name = pd.NA
    env_col = next((c for k, c in cols.items() if k.startswith("ubicaci")), None)
    status_col = next((c for k, c in cols.items() if k.startswith("estado")), None)
    spud_col = next((c for k, c in cols.items() if k.startswith("fecha_inic")), None)
    comp_col = next((c for k, c in cols.items() if k.startswith("fecha_fin")), None)

    out = gpd.GeoDataFrame(
        {
            "source": "mexico",
            "well_id": names,
            "well_name": names,
            "field_id": field_id,
            "field_name": field_name,
            "region": _normalize_mexico_region(gdf[cols["entidad"]])
            if "entidad" in cols
            else pd.NA,
            "environment": _normalize_environment(gdf[env_col] if env_col else None, len(gdf)),
            "status": gdf[status_col] if status_col else pd.NA,
            "spud_date": _parse_spanish_date(gdf[spud_col]) if spud_col else pd.NA,
            "completion_date": _parse_spanish_date(gdf[comp_col]) if comp_col else pd.NA,
        },
        geometry=gdf.geometry,
        crs=gdf.crs or schema.GEO_CRS,
    )
    out = out.loc[out["well_id"].notna() & (out["well_id"] != "")]
    out = out.drop_duplicates(subset=["well_id"], keep="last")
    return geo.prepare_geo(out, schema.GeoKind.WELLS)


@base.register
class Mexico(base.Source):
    """Mexico SENER / CNIH (ex-CNH) SIH production and Mapa Hidrocarburos connector."""

    name = "mexico"
    country = "Mexico"
    country_code = "MX"
    publisher = "SENER / CNIH"
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
        """Return SENER SIH's ``PRODUCCION_CAMPOS.csv`` full-history field production CSV.

        Well-level ``PRODUCCION_POZOS.zip`` (~155 MB) is also supported by
        ``parse_production`` when passed via ``files=``.
        """
        del client, year, environment, recent
        return [
            base.RemoteFile(
                url=_CAMPOS_PROD_URL,
                key="PRODUCCION_CAMPOS.csv",
                year_min=1960,
                year_max=datetime.now(tz=UTC).year,
            )
        ]

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        """Parse either SIH ``PRODUCCION_CAMPOS.csv`` (field) or ``POZOS_COMPILADO.csv`` (well)."""
        frame = _read_sih_csv(raw)
        cols = {_norm_header(c): c for c in frame.columns}
        if "campo_sih" in cols:
            return _parse_campos_frame(frame, cols)
        if "nombre_del_pozo" in cols or "identificador" in cols:
            return _parse_pozos_frame(frame, cols)
        raise ValueError(f"unrecognized Mexico SIH production columns: {sorted(frame.columns)}")

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Export and download shapefiles from Mapa de Hidrocarburos."""
        with base.open_client(client) as http:
            if kind is schema.GeoKind.FIELDS:
                return [
                    _export_map_layer(http, "cmp_cmp_rsv", "campos", dest_dir / "campos.zip"),
                    _export_map_layer(
                        http,
                        _PETRO_PROV_LAYER,
                        "provincias_petroleras",
                        dest_dir / "provincias_petroleras.zip",
                    ),
                ]
            if kind is schema.GeoKind.FACILITIES:
                return [
                    _export_map_layer(
                        http,
                        "v_iftep_instalaciones",
                        "instalaciones",
                        dest_dir / "instalaciones.zip",
                    )
                ]
            if kind is schema.GeoKind.WELLS:
                return [
                    _export_map_layer(http, layer, layer, dest_dir / f"{layer}.zip")
                    for layer in _WELL_ZONE_LAYERS
                ]
        raise ValueError(f"unsupported geo kind: {kind!r}")

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse downloaded Mapa de Hidrocarburos shapefile ZIPs into GeoParquet-ready frames."""
        if kind is schema.GeoKind.FIELDS:
            fields_path, provinces_path = _split_field_paths(paths)
            return _parse_fields_shp(fields_path, provinces_path)
        if kind is schema.GeoKind.FACILITIES:
            return _parse_facilities_shp(paths[0])
        if kind is schema.GeoKind.WELLS:
            return _parse_wells_shps(paths)
        raise ValueError(f"unsupported geo kind: {kind!r}")
