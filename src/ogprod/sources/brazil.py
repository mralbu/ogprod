"""Brazil - ANP monthly well production/injection, fields, platforms/FPSOs, and wells.

All data comes from ANP's open-data portal ("Fase de Desenvolvimento e Produção",
1941-present) and ANP's GeoServer WFS (``gishub.anp.gov.br``, requesting only the
required field-polygon properties via ``propertyName`` to save bandwidth and space):

- Well production & injection (1941-2026, ``mar`` and ``terra``): unified 21-column CSV
- Offshore platforms / FPSOs (``lpo``): coordinates, water depth, oil & gas capacities
- Fields: WFS ``BD_ANP:CAMPOS_PRODUCAO_SIRGAS`` polygons + active/relinquished CSVs
- Wells: ``situacao-pocos-1939-2026.csv`` (DMS coordinates, status, lift method, dates)
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa
import shapely

from ogprod import geo, schema, units
from ogprod.sources import base

_PORTAL_URL = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "fase-de-desenvolvimento-e-producao"
)
_BASE_FILES = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/"
    "arquivos-fase-de-desenvolvimento-e-producao/"
)
_PLATFORMS_URL = _BASE_FILES + "lpo/dados-abertos-plataformas-operacao.csv"
_FIELDS_ACTIVE_URL = _BASE_FILES + "informacoes-sobre-campos/extracao-campo.csv"
_FIELDS_RELINQ_URL = _BASE_FILES + "informacoes-sobre-campos/extracao-campo-devolvido.csv"
_WELLS_STATUS_URL = _BASE_FILES + "informacoes-sobre-pocos/situacao-pocos-1939-2026.csv"

_WFS_FIELDS_PROPS = (
    "GEOMETRY,COD_CAMPO,NOM_CAMPO,NOM_BACIA,OPERADOR_C,"
    "AMBIENTE,ETAPA,FLUIDO_PRI,MED_LAMINA,AREA,DAT_DESCOB,DAT_INICIO"
)
_WFS_FIELDS_URL = (
    "https://gishub.anp.gov.br/geoserver/BD_ANP/ows"
    "?service=WFS&version=1.0.0&request=GetFeature"
    "&typeName=BD_ANP:CAMPOS_PRODUCAO_SIRGAS"
    f"&propertyName={_WFS_FIELDS_PROPS}&outputFormat=application/json"
)

_PROD_LINK_RE = re.compile(
    r'href="(?P<url>https://www\.gov\.br/anp/[^"]*'
    r'arquivos-producao-de-petroleo-e-gas-natural-nacional/(?P<env>pm|pt)/[^"]+\.csv)"',
    re.IGNORECASE,
)
_WELLS_LINK_RE = re.compile(
    r'href="(?P<url>https://www\.gov\.br/anp/[^"]*'
    r'informacoes-sobre-pocos/situacao-pocos[^"]*\.csv)"',
    re.IGNORECASE,
)

_MERGE_RTOL = 5e-3
_MERGE_ATOL = 0.5

_STATE_SUFFIX: dict[str, str] = {
    "alagoas": "AL",
    "amazonas": "AM",
    "bahia": "BA",
    "ceara": "CE",
    "espirito santo": "ES",
    "maranhao": "MA",
    "parana": "PR",
    "rio de janeiro": "RJ",
    "rio grande do norte": "RN",
    "santa catarina": "SC",
    "sao paulo": "SP",
    "sergipe": "SE",
}

_PT_MONTHS: dict[str, str] = {
    "jan": "01",
    "fev": "02",
    "mar": "03",
    "abr": "04",
    "mai": "05",
    "jun": "06",
    "jul": "07",
    "ago": "08",
    "set": "09",
    "out": "10",
    "nov": "11",
    "dez": "12",
}

_MISSING_SENTINELS = frozenset({"", "NAO INFORMADO", "(NAO CADASTRADO)"})

_FACILITY_CODE_SUFFIX_RE = re.compile(r"\s*\((?:P-\d+|MV-\d+)\)\s*$")

_FACILITY_ALIASES: dict[str, str] = {
    "FPSO CIDADE DE ILHA BELA": "FPSO CIDADE DE ILHABELA",
    "PLATAFORMA DE PAMPO-1": "PLATAFORMA DE PAMPO 1",
    "FPSO CIDADE DE CARAGUATATUBA": "FPSO CIDADE DE CARAGUATATUBA - CCG (MV-27)",
}


@dataclass(frozen=True)
class Gap:
    """A known stretch of months where ANP's well-level reporting is incomplete."""

    column: str
    start: date
    end: date
    description: str


KNOWN_GAPS: tuple[Gap, ...] = (
    Gap(
        column="gas_prod",
        start=date(2003, 7, 1),
        end=date(2005, 6, 1),
        description=(
            "only ~600 of ~8,300 producing wells report gas (~5% of national volume); "
            "ogprod warns and never imputes"
        ),
    ),
)

_ENV_MAP = {
    "mar": "offshore",
    "m": "offshore",
    "offshore": "offshore",
    "terra": "onshore",
    "t": "onshore",
    "onshore": "onshore",
}

_DMS_RE = re.compile(
    r"^\s*(?P<sign>[+-]?)\s*(?P<deg>\d{1,3})\s*[:°]\s*"
    r"(?P<min>\d{1,2})\s*[:']\s*(?P<sec>\d{1,2}(?:[.,]\d+)?)\s*\"?\s*$"
)


def _norm_header(name: str) -> str:
    cleaned = name.strip().strip("[]")
    ascii_text = unicodedata.normalize("NFKD", cleaned).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_text).strip().lower()


def parse_br_number(series: pd.Series) -> pd.Series:
    """Parse Brazilian-formatted numbers (dot thousands, comma decimal) to float64."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    text = (
        series.astype("string")
        .str.strip()
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(text, errors="coerce")


def parse_dms_coord(value: str | float | None) -> float | None:
    """Parse ANP DMS coordinate strings (e.g. ``'-12:42:49,384'``) or decimal degrees."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _DMS_RE.match(text)
    if match:
        sign = -1.0 if match.group("sign") == "-" else 1.0
        deg = float(match.group("deg"))
        minutes = float(match.group("min"))
        seconds = float(match.group("sec").replace(",", "."))
        result = sign * (deg + minutes / 60.0 + seconds / 3600.0)
        return result if np.isfinite(result) else None
    try:
        result = float(text.replace(",", "."))
    except ValueError:
        return None
    return result if np.isfinite(result) else None


def _parse_year_span(url: str) -> tuple[int | None, int | None]:
    """Extract ``(year_min, year_max)`` from an ANP CSV URL."""
    tail = url.split("arquivos-producao-de-petroleo-e-gas-natural-nacional/", 1)[-1]
    *dirs, filename = tail.split("/")
    file_years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", filename)]
    if file_years:
        return min(file_years), max(file_years)
    for segment in reversed(dirs):
        dir_years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", segment)]
        if dir_years:
            return min(dir_years), max(dir_years)
    return None, None


def _env_matches(env_code: str, environment: str | None) -> bool:
    if environment is None:
        return True
    wanted = _ENV_MAP.get(environment.strip().lower(), environment.strip().lower())
    actual = "offshore" if env_code.lower() == "pm" else "onshore"
    return wanted == actual


def _read_raw_bytes(raw: base.RawFile) -> bytes:
    if isinstance(raw, str | Path):
        return Path(raw).read_bytes()
    raw.seek(0)
    return raw.read()


def _unwrap_outer_quoted_rows(text: str) -> str:
    """Fix ANP 2019-2021 CSVs where entire data rows are wrapped in outer quotes."""
    lines = text.splitlines()
    if len(lines) <= 1:
        return text
    header = lines[0]
    if "," not in header:
        return text
    out = [header]
    for line in lines[1:]:
        if line.startswith('"'):
            parsed = next(csv.reader([line]), [])
            if len(parsed) == 1 and "," in parsed[0]:
                out.append(parsed[0])
                continue
        out.append(line)
    return "\n".join(out)


def _read_csv_auto(raw: base.RawFile) -> pd.DataFrame:
    """Read an ANP CSV, supporting UTF-8 BOM/cp1252 and outer-quoted 2019-2021 rows."""
    content = _read_raw_bytes(raw)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        cleaned = _unwrap_outer_quoted_rows(text)
        return pd.read_csv(io.StringIO(cleaned), sep=",", dtype=str, low_memory=False)
    raise ValueError("could not decode ANP CSV as utf-8-sig or cp1252")


_PT_PERIOD_RE = re.compile(r"^([a-z]{3})[/-](\d{4})$")


def _normalize_month_year_text(value: object) -> object:
    if pd.isna(value):
        return pd.NA
    text = str(value).strip()
    if not text:
        return pd.NA
    match = _PT_PERIOD_RE.match(text.lower())
    if match and match.group(1) in _PT_MONTHS:
        return f"{_PT_MONTHS[match.group(1)]}/{match.group(2)}"
    return text


def _parse_anp_period_series(series: pd.Series) -> pd.Series:
    """Parse ANP month/year strings (``MM/YYYY``, ``mmm/YYYY`` PT, ``YYYY-MM``) to Timestamp."""
    norm = series.map(_normalize_month_year_text).astype("string")
    parsed = pd.to_datetime(norm, format="%m/%Y", errors="coerce")
    unresolved = norm.notna() & parsed.isna()
    if unresolved.any():
        iso_cand = norm.loc[unresolved].str.replace("/", "-", regex=False)
        parsed = parsed.copy()
        parsed.loc[unresolved] = pd.to_datetime(iso_cand, format="%Y-%m", errors="coerce")
    return parsed


def _extract_period(frame: pd.DataFrame, cols: dict[str, str]) -> pd.Series:
    raw_col = cols.get("mes/ano") or cols.get("periodo")
    if raw_col is None:
        raise ValueError("ANP production CSV missing 'Mês/Ano' or 'Período' column")
    raw_text = frame[raw_col].astype("string").str.strip()
    parsed = _parse_anp_period_series(raw_text)
    bad_mask = raw_text.notna() & (raw_text != "") & parsed.isna()
    if bad_mask.any():
        samples = raw_text.loc[bad_mask].drop_duplicates().head(5).tolist()
        raise ValueError(
            f"ANP production CSV has {int(bad_mask.sum())} unparseable period value(s): {samples}"
        )
    return parsed


def _sum_optional(a: pd.Series | None, b: pd.Series | None, length: int) -> pd.Series:
    if a is None and b is None:
        return pd.Series(np.nan, index=range(length), dtype="float64")
    if a is None:
        assert b is not None
        return b
    if b is None:
        return a
    both_na = a.isna() & b.isna()
    total = a.fillna(0.0) + b.fillna(0.0)
    return total.mask(both_na)


def _compute_anp_rates(
    frame: pd.DataFrame, cols: dict[str, str], days: pd.Series
) -> dict[str, pd.Series]:
    """Convert monthly ANP volumes (m³ liquids, e3m3 gas) to daily rates (bbl/d, e3m3/d)."""
    n = len(frame)
    liq = units.BBL_PER_M3 / days
    gas = 1.0 / days

    def col_num(key: str) -> pd.Series | None:
        return parse_br_number(frame[cols[key]]) if key in cols else None

    oil_v = col_num("producao de oleo (m3)")
    cond_v = col_num("producao de condensado (m3)")
    gas_assoc = col_num("producao de gas associado (mm3)")
    gas_nonassoc = col_num("producao de gas nao associado (mm3)")
    water_v = col_num("producao de agua (m3)")
    winj_sec = col_num("injecao de agua para recuperacao secundaria (m3)")
    winj_disp = col_num("injecao de agua para descarte (m3)")
    ginj_v = col_num("injecao de gas (mm3)")
    co2_v = col_num("injecao de gas carbonico (mm3)")

    rates: dict[str, pd.Series] = {}
    if oil_v is not None:
        rates["oil_prod"] = oil_v * liq
    if cond_v is not None:
        rates["condensate_prod"] = cond_v * liq
    if gas_assoc is not None or gas_nonassoc is not None:
        rates["gas_prod"] = _sum_optional(gas_assoc, gas_nonassoc, n) * gas
    if water_v is not None:
        rates["water_prod"] = water_v * liq
    if winj_sec is not None or winj_disp is not None:
        rates["water_inj"] = _sum_optional(winj_sec, winj_disp, n) * liq
    if ginj_v is not None:
        rates["gas_inj"] = ginj_v * gas
    if co2_v is not None:
        rates["co2_inj"] = co2_v * gas
    return rates


def _state_matches_well(well: pd.Series, state: pd.Series) -> pd.Series:
    """True when the row's Estado matches the 2-letter state suffix on the ANP well name."""
    suffix = well.astype("string").str.rsplit("-", n=1).str[-1].str.strip().str.upper().str[:2]
    norm_state = state.astype("string").map(
        lambda s: _STATE_SUFFIX.get(_norm_header(str(s)), "") if pd.notna(s) else ""
    )
    return suffix == norm_state


def _canonicalize_facility_id(norm_id: str) -> str:
    if norm_id in _MISSING_SENTINELS:
        return ""
    if norm_id in _FACILITY_ALIASES:
        return _FACILITY_ALIASES[norm_id]
    if " - " not in norm_id:
        return _FACILITY_CODE_SUFFIX_RE.sub("", norm_id).strip()
    return norm_id


def _clean_facility(series: pd.Series | None, length: int) -> tuple[pd.Series, pd.Series]:
    if series is None:
        empty = pd.Series([""] * length, dtype="string")
        nulls = pd.Series([pd.NA] * length, dtype="string")
        return empty, nulls
    raw_name = series.astype("string").str.strip()
    norm_id = raw_name.map(lambda v: schema.normalize_key(None if pd.isna(v) else str(v)))
    fac_id = norm_id.map(_canonicalize_facility_id).astype("string")
    fac_name = raw_name.mask(fac_id == "", pd.NA).astype("string")
    return fac_id, fac_name


def _clean_region(series: pd.Series | None, length: int) -> pd.Series:
    if series is None:
        return pd.Series([pd.NA] * length, dtype="string")
    raw = series.astype("string").str.strip()
    norm = raw.map(lambda v: schema.normalize_key(None if pd.isna(v) else str(v)))
    return raw.mask(norm.isin(_MISSING_SENTINELS), pd.NA).astype("string")


def _drop_empty_unassigned_facility(work: pd.DataFrame, rate_cols: list[str]) -> pd.DataFrame:
    """Drop empty ``facility_id == ''`` rows when the same well-field-month has a real facility."""
    has_fac = work["facility_id"] != ""
    if has_fac.all() or not has_fac.any():
        return work
    real_keys = pd.MultiIndex.from_frame(work.loc[has_fac, ["entity_id", "field_id", "period"]])
    all_keys = pd.MultiIndex.from_frame(work[["entity_id", "field_id", "period"]])
    all_zero_or_na = work[rate_cols].fillna(0.0).eq(0.0).all(axis=1)
    redundant = (~has_fac) & all_keys.isin(real_keys) & all_zero_or_na
    if redundant.any():
        return work.loc[~redundant].reset_index(drop=True)
    return work


def _merge_duplicate_rows(work: pd.DataFrame, rate_cols: list[str]) -> pd.DataFrame:
    """Merge known ANP in-file duplicates (e.g. pre-2005 state cross-join) safely."""
    key = ["entity_id", "field_id", "facility_id", "period"]
    repeated = work.duplicated(subset=key, keep=False)
    if not repeated.any():
        return work

    rows = work.loc[repeated].copy()
    rows["_prefer"] = _state_matches_well(rows["entity_id"], rows["region"]).astype(int)
    rows = rows.sort_values("_prefer", ascending=False, kind="mergesort")
    group = rows.groupby(key, dropna=False, sort=False).ngroup()
    first_mask = ~group.duplicated()
    merged = rows.loc[first_mask].drop(columns=["_prefer"]).copy()
    merged.index = group[first_mask].to_numpy()

    for col in rate_cols:
        values = rows[col]
        nonzero = values.where(values != 0).groupby(group)
        low, high, first = nonzero.min(), nonzero.max(), nonzero.first()
        diff = (high - low).abs()
        conflict = (diff > _MERGE_RTOL * high.abs()) & (diff > _MERGE_ATOL)
        if conflict.any():
            sample = merged.loc[conflict[conflict].index, key].head(5).to_dict("records")
            raise schema.SchemaError(f"conflicting duplicate ANP rows for {col!r}: {sample}")
        has_zero = (values == 0).groupby(group).any()
        merged[col] = first.mask(first.isna() & has_zero, 0.0)

    return pd.concat([work.loc[~repeated], merged], ignore_index=True)


def _warn_known_gaps(table: pa.Table) -> None:
    if table.num_rows == 0:
        return
    periods = table.column("period").to_pandas()
    for gap in KNOWN_GAPS:
        vals = table.column(gap.column)
        if vals.null_count == table.num_rows:
            continue
        in_gap = (periods >= gap.start) & (periods <= gap.end)
        if in_gap.any():
            warnings.warn(
                f"ANP {gap.column} is incomplete for {gap.start:%Y-%m}..{gap.end:%Y-%m}: "
                f"{gap.description}",
                UserWarning,
                stacklevel=3,
            )


def _parse_br_date(series: pd.Series) -> pd.Series:
    """Parse Brazilian ``DD/MM/YYYY`` or ``DD-MM-YYYY`` (or ISO ``YYYY-MM-DD``) dates to ISO."""
    text = series.astype("string").str.strip()
    slash = text.str.replace("-", "/", regex=False)
    parsed = pd.to_datetime(slash, format="%d/%m/%Y", errors="coerce")
    unresolved = text.notna() & (text != "") & parsed.isna()
    if unresolved.any():
        parsed = parsed.copy()
        parsed.loc[unresolved] = pd.to_datetime(
            text.loc[unresolved], format="%Y-%m-%d", errors="coerce"
        )
    return parsed.dt.strftime("%Y-%m-%d").astype("string")


def _parse_month_year(series: pd.Series) -> pd.Series:
    parsed = _parse_anp_period_series(series)
    return parsed.dt.strftime("%Y-%m-%d").astype("string")


def _normalize_environment(series: pd.Series | None, length: int) -> pd.Series:
    if series is None:
        return pd.Series([pd.NA] * length, dtype="string")
    return (
        series.astype("string")
        .map(lambda v: _ENV_MAP.get(str(v).strip().lower(), pd.NA) if pd.notna(v) else pd.NA)
        .astype("string")
    )


def _parse_facilities_csv(path: Path) -> gpd.GeoDataFrame:
    df = _read_csv_auto(path)
    cols = {_norm_header(c): c for c in df.columns}
    if "nome da instalacao" not in cols:
        raise ValueError(f"not an ANP LPO platforms CSV; columns: {sorted(df.columns)}")

    names = df[cols["nome da instalacao"]].astype("string").str.strip()
    fac_ids = names.map(lambda v: schema.normalize_key(None if pd.isna(v) else str(v)))
    lat = (
        df[cols["latitude"]].map(parse_dms_coord)
        if "latitude" in cols
        else pd.Series([None] * len(df))
    )
    lon = (
        df[cols["longitude"]].map(parse_dms_coord)
        if "longitude" in cols
        else pd.Series([None] * len(df))
    )
    geoms = [
        shapely.Point(float(x), float(y)) if pd.notna(x) and pd.notna(y) else None
        for x, y in zip(lon, lat, strict=True)
    ]

    gas_cap_col = next(
        (c for k, c in cols.items() if k.startswith("capacidade de producao de gas")), None
    )
    oil_cap_col = next(
        (c for k, c in cols.items() if k.startswith("capacidade de producao de petroleo")), None
    )
    water_depth_col = next((c for k, c in cols.items() if "lamina" in k), None)

    out = gpd.GeoDataFrame(
        {
            "source": "brazil",
            "facility_id": fac_ids,
            "facility_code": df[cols["sigla da instalacao"]]
            if "sigla da instalacao" in cols
            else pd.NA,
            "facility_name": names,
            "facility_type": df[cols["classificacao"]] if "classificacao" in cols else pd.NA,
            "fields": df[cols["campos"]] if "campos" in cols else pd.NA,
            "basin": df[cols["bacia"]] if "bacia" in cols else pd.NA,
            "operator": df[cols["operador"]] if "operador" in cols else pd.NA,
            "status": "Em Operação",
            "start_year": parse_br_number(df[cols["ano de inicio de operacao"]])
            if "ano de inicio de operacao" in cols
            else pd.NA,
            "water_depth_m": parse_br_number(df[water_depth_col]) if water_depth_col else pd.NA,
            "oil_capacity_bpd": parse_br_number(df[oil_cap_col]) if oil_cap_col else pd.NA,
            "gas_capacity_e3m3d": parse_br_number(df[gas_cap_col]) if gas_cap_col else pd.NA,
            "export_system": df[cols["sistema de escoamento"]]
            if "sistema de escoamento" in cols
            else pd.NA,
        },
        geometry=gpd.GeoSeries(geoms, crs=schema.GEO_CRS),
    )
    out = out.loc[out["facility_id"] != ""].drop_duplicates(subset=["facility_id"], keep="first")
    return geo.prepare_geo(out, schema.GeoKind.FACILITIES)


def _parse_fields_wfs(wfs_path: Path) -> gpd.GeoDataFrame:
    gdf = gpd.read_file(wfs_path)
    cols = {_norm_header(c): c for c in gdf.columns}
    if "nom_campo" not in cols:
        raise ValueError(f"not an ANP field polygons file; columns: {sorted(gdf.columns)}")

    gdf = gdf.copy()
    gdf["_field_id"] = gdf[cols["nom_campo"]].map(
        lambda v: schema.normalize_key(None if pd.isna(v) else str(v))
    )
    gdf = gdf.loc[gdf["_field_id"] != ""]
    has_geom = gdf.geometry.notna() & ~gdf.geometry.is_empty
    valid_geom = gdf.loc[has_geom].copy()
    valid_geom = valid_geom.set_geometry(valid_geom.geometry.make_valid())
    dissolved = valid_geom.dissolve(by="_field_id", aggfunc="first").reset_index()
    if dissolved.crs is None:
        dissolved = dissolved.set_crs(schema.GEO_CRS)
    elif dissolved.crs.to_epsg() != 4326:
        dissolved = dissolved.to_crs(schema.GEO_CRS)

    return gpd.GeoDataFrame(
        {
            "source": "brazil",
            "field_id": dissolved["_field_id"],
            "field_code": dissolved[cols["cod_campo"]].astype("string")
            if "cod_campo" in cols
            else pd.NA,
            "field_name": dissolved[cols["nom_campo"]].astype("string"),
            "basin": dissolved[cols["nom_bacia"]] if "nom_bacia" in cols else pd.NA,
            "region": pd.NA,
            "environment": _normalize_environment(
                dissolved[cols["ambiente"]] if "ambiente" in cols else None, len(dissolved)
            ),
            "operator": dissolved[cols["operador_c"]] if "operador_c" in cols else pd.NA,
            "status": dissolved[cols["etapa"]] if "etapa" in cols else pd.NA,
            "primary_fluid": dissolved[cols["fluido_pri"]] if "fluido_pri" in cols else pd.NA,
            "discovery_date": _parse_br_date(dissolved[cols["dat_descob"]])
            if "dat_descob" in cols
            else pd.NA,
            "production_start_date": _parse_br_date(dissolved[cols["dat_inicio"]])
            if "dat_inicio" in cols
            else pd.NA,
            "water_depth_m": dissolved[cols["med_lamina"]] if "med_lamina" in cols else pd.NA,
            "area_km2": dissolved[cols["area"]] if "area" in cols else pd.NA,
        },
        geometry=dissolved.geometry,
        crs=schema.GEO_CRS,
    )


def _read_fields_meta_csv(path: Path) -> pd.DataFrame | None:
    df = _read_csv_auto(path)
    cols = {_norm_header(c): c for c in df.columns}
    if "campo" not in cols:
        return None
    status_col = cols.get("situacao_campo") or cols.get("situacao")
    return pd.DataFrame(
        {
            "field_id": df[cols["campo"]].map(
                lambda v: schema.normalize_key(None if pd.isna(v) else str(v))
            ),
            "field_code": df[cols["codigo_campo"]] if "codigo_campo" in cols else pd.NA,
            "field_name": df[cols["campo"]],
            "basin": df[cols["bacia"]] if "bacia" in cols else pd.NA,
            "region": df[cols["estado"]] if "estado" in cols else pd.NA,
            "environment": _normalize_environment(
                df[cols["ambiente"]] if "ambiente" in cols else None, len(df)
            ),
            "operator": df[cols["operador"]] if "operador" in cols else pd.NA,
            "status": df[status_col] if status_col else "Devolvido",
            "production_start_date": _parse_br_date(df[cols["data_inicio_producao"]])
            if "data_inicio_producao" in cols
            else pd.NA,
        }
    )


def _enrich_fields_from_csv(
    base_fields: gpd.GeoDataFrame, csv_paths: list[Path]
) -> gpd.GeoDataFrame:
    """Add region/env/dates and relinquished historical fields (null geometry) from ANP CSVs."""
    frames = [frame for path in csv_paths if (frame := _read_fields_meta_csv(path)) is not None]
    if not frames:
        return base_fields
    meta = pd.concat(frames, ignore_index=True)
    meta = meta.loc[meta["field_id"] != ""].drop_duplicates(subset=["field_id"], keep="first")

    lookup = meta.set_index("field_id")
    enriched = base_fields.copy()
    for col in ("region", "environment", "operator", "production_start_date"):
        enriched[col] = enriched[col].fillna(enriched["field_id"].map(lookup[col]))

    missing = meta.loc[~meta["field_id"].isin(enriched["field_id"])].copy()
    if len(missing) == 0:
        return enriched
    missing["source"] = "brazil"
    extra = gpd.GeoDataFrame(
        missing,
        geometry=gpd.GeoSeries([None] * len(missing), crs=schema.GEO_CRS),
    )
    return gpd.GeoDataFrame(
        pd.concat([enriched, extra], ignore_index=True), geometry="geometry", crs=enriched.crs
    )


def _parse_wells_csv(path: Path) -> gpd.GeoDataFrame:
    df = _read_csv_auto(path)
    cols = {_norm_header(c): c for c in df.columns}
    name_col = next(
        (cols[k] for k in ("nome_poco_anp", "nome poco anp", "poco") if k in cols), None
    )
    if name_col is None:
        raise ValueError(f"not an ANP wells CSV; columns: {sorted(df.columns)}")

    names = df[name_col].astype("string").str.strip()
    lat_col = next((cols[k] for k in ("latitude_da_base", "latitude_base_dd") if k in cols), None)
    lon_col = next((cols[k] for k in ("longitude_da_base", "longitude_base_dd") if k in cols), None)
    if lat_col is None or lon_col is None:
        raise ValueError(f"ANP wells CSV missing coordinate columns; got: {sorted(df.columns)}")

    lat = df[lat_col].map(parse_dms_coord)
    lon = df[lon_col].map(parse_dms_coord)
    geoms = [
        shapely.Point(float(x), float(y)) if pd.notna(x) and pd.notna(y) else None
        for x, y in zip(lon, lat, strict=True)
    ]

    field_col = cols.get("campo")
    field_name = (
        df[field_col].astype("string").str.strip() if field_col else pd.Series([pd.NA] * len(df))
    )
    field_id = field_name.map(lambda v: schema.normalize_key(None if pd.isna(v) else str(v)))
    prod_end_col = next((c for k, c in cols.items() if k.startswith("data_ultima_produca")), None)

    out = gpd.GeoDataFrame(
        {
            "source": "brazil",
            "well_id": names,
            "well_name": names,
            "field_id": field_id.mask(field_id == "", pd.NA),
            "field_name": field_name.mask(field_name == "", pd.NA),
            "basin": df[cols["bacia"]] if "bacia" in cols else pd.NA,
            "region": df[cols["estado"]] if "estado" in cols else pd.NA,
            "environment": _normalize_environment(
                df[cols["ambiente"]] if "ambiente" in cols else None, len(df)
            ),
            "operator": df[cols["operador_atual"]] if "operador_atual" in cols else pd.NA,
            "status": df[cols["situacao_do_poco"]] if "situacao_do_poco" in cols else pd.NA,
            "lift_method": df[cols["metodo_de_elevacao"]]
            if "metodo_de_elevacao" in cols
            else pd.NA,
            "spud_date": _parse_br_date(df[cols["data_inicio_perfuracao"]])
            if "data_inicio_perfuracao" in cols
            else pd.NA,
            "completion_date": _parse_br_date(df[cols["data_conclusao_poco"]])
            if "data_conclusao_poco" in cols
            else pd.NA,
            "production_start": _parse_month_year(df[cols["data_inicio_producao_(mes/ano)"]])
            if "data_inicio_producao_(mes/ano)" in cols
            else pd.NA,
            "production_end": _parse_month_year(df[prod_end_col]) if prod_end_col else pd.NA,
        },
        geometry=gpd.GeoSeries(geoms, crs=schema.GEO_CRS),
    )
    out = out.loc[out["well_id"].notna() & (out["well_id"] != "")]
    out = out.drop_duplicates(subset=["well_id"], keep="last")
    return geo.prepare_geo(out, schema.GeoKind.WELLS)


@base.register
class Brazil(base.Source):
    """Brazil ANP open-data connector (1941-present)."""

    name = "brazil"
    country = "Brazil"
    country_code = "BR"
    publisher = "ANP"
    entity_type = schema.EntityType.WELL
    docs_url = _PORTAL_URL

    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[base.RemoteFile]:
        """Discover onshore and offshore production CSVs from ANP's portal page."""
        with base.open_client(client) as http:
            html = base.get_with_retry(http, _PORTAL_URL).text

        seen: set[str] = set()
        remotes: list[base.RemoteFile] = []
        current_year = datetime.now(tz=UTC).year
        for match in _PROD_LINK_RE.finditer(html):
            url = match.group("url")
            env_code = match.group("env").lower()
            if url in seen or not _env_matches(env_code, environment):
                continue
            seen.add(url)
            y_min, y_max = _parse_year_span(url)
            if year is not None and not _covers_year(y_min, y_max, year):
                continue
            if recent and y_max is not None and y_max < current_year - 1:
                continue
            tail = url.split("arquivos-producao-de-petroleo-e-gas-natural-nacional/", 1)[-1]
            key = tail.lstrip("/") or f"{env_code}/{Path(url).name}"
            remotes.append(base.RemoteFile(url=url, key=key, year_min=y_min, year_max=y_max))
        if not remotes:
            raise ValueError(
                f"no ANP production files matched "
                f"(year={year!r}, environment={environment!r}, recent={recent!r})"
            )
        return sorted(remotes, key=lambda r: (r.year_min or 0, r.key))

    def parse_production(self, raw: base.RawFile) -> pa.Table:
        """Parse one ANP production/injection CSV into the canonical wide ``float32`` table."""
        frame = _read_csv_auto(raw)
        cols = {_norm_header(c): c for c in frame.columns}
        if "poco" not in cols:
            raise ValueError(
                f"not an ANP production CSV; missing 'Poço' column: {sorted(frame.columns)}"
            )

        raw_rows = len(frame)
        period = _extract_period(frame, cols)
        valid = period.notna() & frame[cols["poco"]].notna()
        frame = frame.loc[valid].reset_index(drop=True)
        period = period.loc[valid].reset_index(drop=True)
        if len(frame) == 0:
            if raw_rows > 0:
                raise ValueError(
                    f"ANP production CSV had {raw_rows} row(s) but 0 valid well/period rows"
                )
            return schema.empty_production()

        days = period.dt.days_in_month.astype("float64")
        rates = _compute_anp_rates(frame, cols, days)
        rate_cols = list(rates)

        field_raw = (
            frame[cols["campo"]].astype("string").str.strip()
            if "campo" in cols
            else pd.Series([pd.NA] * len(frame))
        )
        field_id = field_raw.map(lambda v: schema.normalize_key(None if pd.isna(v) else str(v)))
        fac_id, fac_name = _clean_facility(
            frame[cols["instalacao"]] if "instalacao" in cols else None, len(frame)
        )

        work = pd.DataFrame(
            {
                "entity_id": frame[cols["poco"]].astype("string").str.strip(),
                "field_id": field_id,
                "field_name": field_raw.mask(field_raw == "", pd.NA),
                "facility_id": fac_id,
                "facility_name": fac_name,
                "basin": frame[cols["bacia"]].astype("string").str.strip()
                if "bacia" in cols
                else pd.NA,
                "region": _clean_region(
                    frame[cols["estado"]] if "estado" in cols else None, len(frame)
                ),
                "environment": _normalize_environment(
                    frame[cols["ambiente"]] if "ambiente" in cols else None, len(frame)
                ),
                "period": period,
                **rates,
            }
        )
        work = _drop_empty_unassigned_facility(work, rate_cols)
        work = _merge_duplicate_rows(work, rate_cols)

        table = base.build_production_table(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=work["entity_id"],
            entity_name=work["entity_id"],
            period=work["period"],
            rates={col: work[col] for col in rate_cols},
            field_id=work["field_id"],
            field_name=work["field_name"],
            facility_id=work["facility_id"],
            facility_name=work["facility_name"],
            basin=work["basin"],
            region=work["region"],
            environment=work["environment"],
        )
        _warn_known_gaps(table)
        return table

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[base.DownloadResult]:
        """Download ANP spatial data for ``fields``, ``facilities``, or ``wells``."""
        with base.open_client(client) as http:
            if kind is schema.GeoKind.FACILITIES:
                return [base.download_file(http, _PLATFORMS_URL, dest_dir / "plataformas.csv")]
            if kind is schema.GeoKind.WELLS:
                wells_url = _discover_wells_url(http)
                return [base.download_file(http, wells_url, dest_dir / "situacao_pocos.csv")]
            if kind is schema.GeoKind.FIELDS:
                return [
                    base.download_file(http, _WFS_FIELDS_URL, dest_dir / "campos.geojson"),
                    base.download_file(http, _FIELDS_ACTIVE_URL, dest_dir / "extracao_campo.csv"),
                    base.download_file(
                        http, _FIELDS_RELINQ_URL, dest_dir / "extracao_campo_devolvido.csv"
                    ),
                ]
        raise ValueError(f"unsupported geo kind: {kind!r}")

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Parse downloaded ANP spatial files into a validated GeoParquet-ready GeoDataFrame."""
        if kind is schema.GeoKind.FACILITIES:
            return _parse_facilities_csv(paths[0])
        if kind is schema.GeoKind.WELLS:
            return _parse_wells_csv(paths[0])
        if kind is schema.GeoKind.FIELDS:
            wfs_file = next(
                (p for p in paths if p.suffix.lower() in {".geojson", ".json"}), paths[0]
            )
            csv_files = [p for p in paths if p != wfs_file]
            base_fields = _parse_fields_wfs(wfs_file)
            enriched = _enrich_fields_from_csv(base_fields, csv_files)
            return geo.prepare_geo(enriched, schema.GeoKind.FIELDS)
        raise ValueError(f"unsupported geo kind: {kind!r}")


def _discover_wells_url(http: httpx2.Client) -> str:
    """Discover the current ANP wells CSV URL from the portal, falling back to the default."""
    try:
        resp = http.get(_PORTAL_URL)
        resp.raise_for_status()
    except httpx2.HTTPError:
        return _WELLS_STATUS_URL
    match = _WELLS_LINK_RE.search(resp.text)
    return match.group("url") if match else _WELLS_STATUS_URL


def _covers_year(y_min: int | None, y_max: int | None, year: int) -> bool:
    if y_min is None or y_max is None:
        return False
    return y_min <= year <= y_max
