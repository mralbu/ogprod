"""Brazil — ANP monthly production per well.

ANP publishes monthly well-level production since 2010 as semicolon-separated
CSV files with comma decimals, split into onshore ("terra"), offshore ("mar")
and pre-salt ("pré-sal") files. Data from 2023 onward is also queryable in the
CDP application.

Column headers have varied over the years (accents, unit suffixes, "Ano"/"Mês"
vs a single "Período" column), so this connector matches headers after
normalizing accents and case, and reads the volume unit out of the header's
parenthesis — including daily-rate units like ``(bbl/dia)``, which are
multiplied by the number of days in the month.

Downloads: https://dados.gov.br/dados/conjuntos-dados/producao-de-petroleo-e-gas-natural-por-poco
"""

from __future__ import annotations

import re
import shutil
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx
import pandas as pd

from ogprod import units
from ogprod.schema import EntityType, Product, month_start
from ogprod.sources import base

_UNIT_ALIASES = {
    "m3": "m3",
    "mm3": "e3m3",  # ANP writes "Mm³" for mil (thousand) m³
    "mmm3": "e6m3",
    "bbl": "bbl",
    "mbbl": "mbbl",
}

_PRODUCT_PREFIXES: list[tuple[str, Product]] = [
    ("petroleo", Product.OIL),
    ("oleo", Product.OIL),
    ("condensado", Product.CONDENSATE),
    ("gas natural", Product.GAS),
    ("agua", Product.WATER),
]

_META_ALIASES = {
    "poco": "entity",
    "nome poco": "entity",
    "nome poco anp": "entity",
    "campo": "field",
    "nome campo": "field",
    "bacia": "basin",
    "nome bacia": "basin",
    "estado": "region",
    "uf": "region",
    "operador": "operator",
}


# gov.br hosts bulk zips (yearly 2005-2020, monthly 2021-2023) linked from this
# page; from 2024 on the data is only in the interactive CDP application.
_PAGE_URL = (
    "https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/"
    "producao-de-petroleo-e-gas-natural-por-poco"
)
_CDP_URL = "https://cdp.anp.gov.br/"
_ZIP_LINK = re.compile(
    r'href="(?P<url>[^"]*arquivos-producao-de-petroleo-e-gas-natural-por-poco/[^"]+\.zip)"'
)


# Well cadastre — one row per drilled well with base coordinates
# (SIRGAS2000, equivalent to WGS84 at this scale). The "Tabela de Poços"
# download page documented by ANP no longer resolves (404 as of 2026-07); the
# live "Acervo de Dados" page instead links this GeoServer WFS query, which
# returns the full cadastre (~31k wells) as CSV with a WKT point geometry
# column ("GEOM"). ANP also separately publishes yearly "poços públicos"
# CSVs (declassified wells only, a few hundred rows/year) in the
# semicolon/comma-decimal style with explicit LATITUDE_BASE_DD/
# LONGITUDE_BASE_DD columns documented in the original brief — parse_entities
# accepts either shape. Extend these aliases/URL if a live download differs
# again.
_CADASTRE_WFS_URL = (
    "https://gishub.anp.gov.br/geoserver/BD_ANP/ows?service=WFS&version=1.0.0"
    "&request=GetFeature&typeName=BD_ANP:POCOS_SIRGAS&outputFormat=csv"
)

_ENTITY_META_ALIASES = {
    "poco": "entity_name",
    "nome poco": "entity_name",
    "nome poco anp": "entity_name",
    "campo": "field",
    "nome campo": "field",
    "bacia": "basin",
    "nome bacia": "basin",
    "estado": "region",
    "uf": "region",
    "operador": "operator",
    "situacao": "status",
    "situacao poco": "status",
}
_LATITUDE_ALIASES = ("latitude_base_dd", "latitude base dd", "latitude base (dd)", "latitude dd")
_LONGITUDE_ALIASES = (
    "longitude_base_dd",
    "longitude base dd",
    "longitude base (dd)",
    "longitude dd",
)
_GEOMETRY_ALIASES = ("geom", "geometry", "the_geom")


def _read_entities(raw: base.RawFile) -> pd.DataFrame:
    """Read a cadastre CSV, tolerating the WFS export's comma-delimited,
    period-decimal style as well as the semicolon/comma-decimal style used
    elsewhere in ANP's bulk files. Like ``_read``, falls back to cp1252 —
    some ANP vintages are cp1252-encoded."""
    try:
        return _read_entities_encoded(raw, "utf-8-sig")
    except UnicodeDecodeError:
        if not isinstance(raw, str | Path):
            raw.seek(0)
        return _read_entities_encoded(raw, "cp1252")


def _read_entities_encoded(raw: base.RawFile, encoding: str) -> pd.DataFrame:
    if isinstance(raw, str | Path):
        with open(raw, encoding=encoding, errors="ignore") as handle:
            first_line = handle.readline()
    else:
        position = raw.tell()
        chunk = raw.readline()
        raw.seek(position)
        first_line = chunk.decode(encoding, errors="ignore") if isinstance(chunk, bytes) else chunk
    semicolon_delimited = first_line.count(";") > first_line.count(",")
    sep, decimal = (";", ",") if semicolon_delimited else (",", ".")
    return pd.read_csv(raw, sep=sep, decimal=decimal, encoding=encoding, low_memory=False)


def _link_period(url: str) -> tuple[int, int | None] | None:
    """Extract (year, month) from a bulk-file URL, tolerating naming drift.

    The filename convention has changed over the years (``2021_01_producao.zip``,
    ``2021-08-producao.zip``, ``producao-2022-01.zip``, ``2023/producao-01.zip``,
    yearly ``producao-por-poco-2019.zip``), so instead of one pattern we read the
    4-digit number as the year (falling back to a year directory segment) and
    the first 1-2 digit number in the filename as the month.
    """
    tail = url.split("arquivos-producao-de-petroleo-e-gas-natural-por-poco/", 1)[-1]
    *directories, filename = tail.split("/")
    numbers = re.findall(r"\d+", filename)
    year = next((int(n) for n in numbers if len(n) == 4), None)
    if year is None:
        year = next((int(d) for d in directories if re.fullmatch(r"\d{4}", d)), None)
    if year is None:
        return None
    month = next((int(n) for n in numbers if len(n) <= 2 and 1 <= int(n) <= 12), None)
    return year, month


def _extract_csvs(zip_path: Path, target_dir: Path) -> list[Path]:
    """Extract the CSV members of ``zip_path`` (flattened) into ``target_dir``."""
    extracted = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            name = Path(member).name
            if not name.lower().endswith(".csv"):
                continue
            target = target_dir / name
            with archive.open(member) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            extracted.append(target)
    return extracted


def _normalize(header: str) -> str:
    text = unicodedata.normalize("NFKD", header).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_volume_header(header: str) -> tuple[Product, str, bool, str] | None:
    """Return (product, unit, is_daily_rate, matched_prefix) for a volume column."""
    normalized = _normalize(header)
    match = re.search(r"\(([^)]+)\)", normalized)
    if not match:
        return None
    unit_text = match.group(1).replace(" ", "")
    is_rate = unit_text.endswith(("/dia", "/d"))
    unit_text = re.sub(r"/(dia|d)$", "", unit_text)
    unit = _UNIT_ALIASES.get(unit_text)
    if unit is None:
        return None
    label = normalized[: match.start()].strip()
    for prefix, product in _PRODUCT_PREFIXES:
        if label.startswith(prefix):
            return product, unit, is_rate, prefix
    return None


def _find_header(head: pd.DataFrame) -> tuple[int, int]:
    """Locate the header block: (rows to skip before it, rows it spans).

    The 2005-2020 yearly archives open with a SIGEP report banner, so the main
    header is the first row containing a period-ish cell ("Período"/"Ano"),
    not necessarily row 0. Sub-header rows below it (empty period cell) belong
    to the header too.
    """
    for start in range(len(head)):
        period_column = next(
            (
                i
                for i, cell in enumerate(head.iloc[start])
                if _normalize(str(cell)) in ("periodo", "ano")
            ),
            None,
        )
        if period_column is None:
            continue
        rows = 1
        while start + rows < len(head) and pd.isna(head.iloc[start + rows, period_column]):
            rows += 1
        return start, rows
    return 0, 1


def _combine_headers(head: pd.DataFrame) -> list[str]:
    """Flatten stacked header rows into one name per column.

    The main row's labels span their sub-columns (like merged spreadsheet
    cells), so they are forward-filled before joining with the sub-labels:
    ``Nome Poço;; / ;ANP;Operador`` becomes ``Nome Poço ANP``/``Nome Poço
    Operador``. Duplicate results get a positional suffix (pandas rejects
    duplicate names).
    """
    top = head.iloc[0].ffill()
    names = []
    for i in range(len(head.columns)):
        parts = [str(top.iloc[i])] if not pd.isna(top.iloc[i]) else []
        parts += [str(cell) for cell in head.iloc[1:, i] if not pd.isna(cell)]
        names.append(" ".join(parts))
    seen: set[str] = set()
    unique = []
    for i, name in enumerate(names):
        unique.append(name if name and name not in seen else f"{name} .{i}")
        seen.add(name)
    return unique


# A month period cell as written in bulk files ("2023/01") or newer exports
# ("2023-01").
_PERIOD_PATTERN = r"\s*\d{4}\s*[/-]\s*\d{1,2}\s*$"


def _read(raw: base.RawFile) -> pd.DataFrame:
    """Read an ANP CSV, flattening the bulk files' multi-row header.

    Most files are utf-8 with a BOM, but some vintages (late 2023) are
    cp1252-encoded.
    """
    try:
        return _read_encoded(raw, "utf-8-sig")
    except UnicodeDecodeError:
        if not isinstance(raw, str | Path):
            raw.seek(0)
        return _read_encoded(raw, "cp1252")


def _read_encoded(raw: base.RawFile, encoding: str) -> pd.DataFrame:
    # names=range(...) pads the sniff to a fixed width so a preamble row
    # narrower than the header doesn't derail pandas' width inference.
    head = pd.read_csv(
        raw, sep=";", encoding=encoding, dtype=str, header=None, nrows=10, names=range(128)
    )
    if not isinstance(raw, str | Path):
        raw.seek(0)  # rewind an open file for the second, full read
    start, header_rows = _find_header(head)
    block = head.iloc[start : start + header_rows]
    width = max(i for i in range(len(block.columns)) if block.iloc[:, i].notna().any()) + 1
    frame = pd.read_csv(
        raw,
        sep=";",
        decimal=",",
        thousands=".",
        encoding=encoding,
        skiprows=start + header_rows,
        names=_combine_headers(block.iloc[:, :width]),
        low_memory=False,
    )
    return _realign(frame)


def _realign(frame: pd.DataFrame) -> pd.DataFrame:
    """Fix files whose data rows sit left of their header.

    2010-2011 "terra" files carry an Estado header column that the data rows
    omit, shifting every value one column left of its name. Detected by the
    period pattern living in a different column than the "Período" header;
    fixed by dropping the leading header name(s) and trailing empty column(s).
    """
    names = list(frame.columns)
    header_index = next((i for i, c in enumerate(names) if _normalize(c) == "periodo"), None)
    if header_index is None:
        return frame

    def period_ratio(index: int) -> float:
        values = frame.iloc[:500, index].dropna().astype(str)
        return values.str.match(_PERIOD_PATTERN).mean() if len(values) else 0.0

    if period_ratio(header_index) >= 0.5:
        return frame
    data_index = next((i for i in range(len(names)) if period_ratio(i) >= 0.5), None)
    if data_index is None or data_index >= header_index:
        return frame
    delta = header_index - data_index
    realigned = frame.iloc[:, : len(names) - delta].copy()
    realigned.columns = names[delta:]
    return realigned


def _period(frame: pd.DataFrame, columns: dict[str, str]) -> pd.Series:
    if "periodo" in columns:
        # Written "2023/01" in bulk files, "2023-01" elsewhere.
        text = frame[columns["periodo"]].astype(str).str.strip().str.replace("/", "-")
        return pd.to_datetime(text, format="%Y-%m")
    if "ano" in columns and "mes" in columns:
        return month_start(frame[columns["ano"]], frame[columns["mes"]])
    raise ValueError("ANP file has neither a 'Período' column nor 'Ano'/'Mês' columns")


@base.register
class ANP(base.Source):
    name = "anp"
    entity_type = EntityType.WELL
    docs_url = (
        "https://dados.gov.br/dados/conjuntos-dados/producao-de-petroleo-e-gas-natural-por-poco"
    )

    def fetch(
        self,
        dest_dir: str | Path,
        *,
        year: int | None = None,
        month: int | None = None,
        client: httpx.Client | None = None,
        **kwargs: Any,
    ) -> list[Path]:
        """Download bulk zip(s) and extract their CSVs.

        Yearly zips exist for 2005-2020, monthly zips for 2021-2023. Omitting
        ``month`` for a monthly-only year downloads that whole year; omitting
        ``year`` downloads everything. One zip bundles several CSVs (terra,
        mar, pré-sal) and every extracted CSV path is returned; when more than
        one zip is fetched, each extracts into a ``<year>``/``<year>-<month>``
        subdirectory of ``dest_dir``.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        extracted = []
        with base.open_client(client) as http:
            response = http.get(_PAGE_URL)
            response.raise_for_status()
            periods: dict[tuple[int, int | None], str] = {}
            for match in _ZIP_LINK.finditer(response.text):
                url = match.group("url")
                period = _link_period(url)
                if period is not None:
                    periods.setdefault(period, url)

            if year is None:
                selected = sorted(periods.items())
            elif month is None and (year, None) not in periods:
                selected = sorted(item for item in periods.items() if item[0][0] == year)
            else:
                selected = (
                    [((year, month), periods[(year, month)])] if (year, month) in periods else []
                )
            if not selected:
                self._raise_no_bulk_file(year, month, periods)

            for (zip_year, zip_month), url in selected:
                if len(selected) == 1:
                    target_dir = dest_dir
                else:
                    label = f"{zip_year}" if zip_month is None else f"{zip_year}-{zip_month:02d}"
                    target_dir = dest_dir / label
                    target_dir.mkdir(exist_ok=True)
                zip_path = base.download(http, url, dest_dir / url.rsplit("/", 1)[-1])
                extracted += _extract_csvs(zip_path, target_dir)
                zip_path.unlink()
        if not extracted:
            raise ValueError("ANP bulk file(s) contained no CSVs")
        return sorted(extracted)

    def _raise_no_bulk_file(
        self, year: int | None, month: int | None, periods: dict[tuple[int, int | None], str]
    ) -> None:
        latest = max((y for y, _ in periods), default=None)
        if year is not None and latest is not None and year > latest:
            raise ValueError(
                f"ANP publishes bulk files on gov.br only through {latest}; "
                f"data for {year} is only queryable in the CDP application ({_CDP_URL})"
            )
        wanted = f"{year}" if month is None else f"{year}-{month:02d}"
        raise ValueError(
            f"no ANP bulk file for {wanted}; "
            f"periods available: {sorted(periods)} (see {self.docs_url})"
        )

    def fetch_entities(
        self, dest_dir: str | Path, *, client: httpx.Client | None = None, **kwargs: Any
    ) -> list[Path]:
        """Download the well cadastre CSV (one row per drilled well, with coordinates)."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            return [base.download(http, _CADASTRE_WFS_URL, dest_dir / "pocos_sirgas.csv")]

    def parse_entities(self, raw: base.RawFile) -> gpd.GeoDataFrame:
        frame = _read_entities(raw)
        normalized = {_normalize(column): column for column in frame.columns}

        meta: dict[str, pd.Series] = {}
        for alias, role in _ENTITY_META_ALIASES.items():
            if alias in normalized and role not in meta:
                meta[role] = frame[normalized[alias]]
        if "entity_name" not in meta:
            raise ValueError(
                "not an ANP well cadastre file; needs a 'Poço' column, "
                f"got: {sorted(frame.columns)}"
            )

        geom_column = next((normalized[a] for a in _GEOMETRY_ALIASES if a in normalized), None)
        latitude = next((normalized[a] for a in _LATITUDE_ALIASES if a in normalized), None)
        longitude = next((normalized[a] for a in _LONGITUDE_ALIASES if a in normalized), None)
        if geom_column is not None:
            wkt = frame[geom_column].where(frame[geom_column].notna(), None)
            geometry = gpd.GeoSeries.from_wkt(wkt)
        elif latitude is not None and longitude is not None:
            lon = pd.to_numeric(frame[longitude], errors="coerce")
            lat = pd.to_numeric(frame[latitude], errors="coerce")
            points = gpd.GeoSeries(gpd.points_from_xy(lon, lat))
            geometry = points.where(~(lon.isna() | lat.isna()), None)
        else:
            raise ValueError(
                "not an ANP well cadastre file; needs a geometry (WKT) column or "
                f"decimal-degree lat/lon columns, got: {sorted(frame.columns)}"
            )

        name = meta["entity_name"]
        return base.build_entities(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=name,
            entity_name=name,
            geometry=geometry,
            field=meta.get("field"),
            basin=meta.get("basin"),
            region=meta.get("region"),
            operator=meta.get("operator"),
            status=meta.get("status"),
        )

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        frame = _read(raw)
        normalized = {_normalize(column): column for column in frame.columns}
        if "periodo" in normalized:
            # Some files concatenate report sections, repeating the banner and
            # header mid-file; keep only rows with a real period cell.
            period_text = frame[normalized["periodo"]].astype(str)
            frame = frame[period_text.str.match(_PERIOD_PATTERN, na=False)].reset_index(drop=True)
        # Some files repeat a row verbatim (e.g. 2015_producao_presal.csv,
        # identical down to contract and installation). Only such exact
        # repeats are dropped; rows sharing a key with different values still
        # fail validation rather than silently losing volume.
        frame = frame.drop_duplicates(ignore_index=True)

        meta: dict[str, pd.Series] = {}
        for alias, role in _META_ALIASES.items():
            if alias in normalized and role not in meta:
                meta[role] = frame[normalized[alias]]
        if "entity" not in meta:
            raise ValueError("ANP file has no well name column ('Poço')")

        period = _period(frame, normalized)
        days_in_month = period.dt.days_in_month

        matched = [
            (column, parsed)
            for column in frame.columns
            if (parsed := _parse_volume_header(column)) is not None
        ]
        # "Petróleo" is the óleo+condensado total; in files that also carry the
        # "Óleo" component, counting both would double-count oil.
        oil_prefixes = {prefix for _, (product, _, _, prefix) in matched if product is Product.OIL}
        skip_prefixes = {"petroleo"} if {"oleo", "petroleo"} <= oil_prefixes else set()
        # Likewise a "Total" sub-column ("Gás Total") supersedes its component
        # sub-columns (associated/non-associated gas).
        products_with_total = {
            product
            for column, (product, _, _, _) in matched
            if "total" in _normalize(column).split()
        }

        volumes: dict[Product, pd.Series] = {}
        for column, (product, unit, is_rate, prefix) in matched:
            if prefix in skip_prefixes:
                continue
            if product in products_with_total and "total" not in _normalize(column).split():
                continue
            raw_values = frame[column]
            if not pd.api.types.is_numeric_dtype(raw_values):
                # Stray header rows keep the column textual, so the read's
                # decimal/thousands handling didn't apply; redo it here.
                raw_values = (
                    raw_values.astype(str)
                    .str.replace(".", "", regex=False)
                    .str.replace(",", ".", regex=False)
                )
            values = pd.to_numeric(raw_values, errors="coerce")
            if is_rate:
                values = values * days_in_month
            values = values * units.factor(unit)
            # A product can appear in several columns (e.g. associated and
            # non-associated gas); sum them.
            volumes[product] = (
                values if product not in volumes else volumes[product].add(values, fill_value=0.0)
            )

        return base.build_frame(
            source=self.name,
            entity_type=self.entity_type,
            entity_id=meta["entity"],
            entity_name=meta["entity"],
            period=period,
            volumes_m3=volumes,
            field=meta.get("field"),
            basin=meta.get("basin"),
            region=meta.get("region"),
            operator=meta.get("operator"),
        )
