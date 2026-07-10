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


def _header_row_count(head: pd.DataFrame) -> int:
    """Number of header rows: 1 plus sub-header rows (empty period cell)."""
    period_column = next(
        (i for i, cell in enumerate(head.iloc[0]) if _normalize(str(cell)) in ("periodo", "ano")),
        None,
    )
    if period_column is None:
        return 1
    rows = 1
    while rows < len(head) and pd.isna(head.iloc[rows, period_column]):
        rows += 1
    return rows


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


def _read(raw: base.RawFile) -> pd.DataFrame:
    """Read an ANP CSV, flattening the bulk files' multi-row header."""
    head = pd.read_csv(raw, sep=";", encoding="utf-8-sig", dtype=str, header=None, nrows=4)
    if not isinstance(raw, str | Path):
        raw.seek(0)  # rewind an open file for the second, full read
    header_rows = _header_row_count(head)
    return pd.read_csv(
        raw,
        sep=";",
        decimal=",",
        thousands=".",
        encoding="utf-8-sig",
        skiprows=header_rows,
        names=_combine_headers(head.iloc[:header_rows]),
    )


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
        """Download the bulk zip for a period and extract its CSVs.

        Monthly zips exist for 2021-2023 (pass ``month``), yearly zips for
        2005-2020 (omit ``month``). One zip bundles several CSVs (terra, mar,
        pré-sal); every extracted CSV path is returned.
        """
        if year is None:
            raise TypeError("anp fetch requires a year, e.g. fetch(dest, year=2023, month=1)")
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with base.open_client(client) as http:
            response = http.get(_PAGE_URL)
            response.raise_for_status()
            periods: dict[tuple[int, int | None], str] = {}
            for match in _ZIP_LINK.finditer(response.text):
                url = match.group("url")
                period = _link_period(url)
                if period is not None:
                    periods.setdefault(period, url)
            url = periods.get((year, month))
            if url is None:
                latest = max((y for y, _ in periods), default=None)
                if latest is not None and year > latest:
                    raise ValueError(
                        f"ANP publishes bulk files on gov.br only through {latest}; "
                        f"data for {year} is only queryable in the CDP application "
                        f"({_CDP_URL})"
                    )
                wanted = f"{year}" if month is None else f"{year}-{month:02d}"
                raise ValueError(
                    f"no ANP bulk file for {wanted}; "
                    f"periods available: {sorted(periods)} (see {self.docs_url})"
                )
            zip_path = base.download(http, url, dest_dir / url.rsplit("/", 1)[-1])

        extracted = []
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.namelist():
                name = Path(member).name
                if not name.lower().endswith(".csv"):
                    continue
                target = dest_dir / name
                with archive.open(member) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                extracted.append(target)
        zip_path.unlink()
        if not extracted:
            raise ValueError(f"ANP bulk file {url} contained no CSVs")
        return sorted(extracted)

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        frame = _read(raw)
        normalized = {_normalize(column): column for column in frame.columns}

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
            values = pd.to_numeric(frame[column], errors="coerce")
            if is_rate:
                values = values * days_in_month
            values = values.map(lambda v, u=unit: units.to_m3(v, u))
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
