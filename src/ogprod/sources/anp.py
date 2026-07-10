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
import unicodedata

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


def _normalize(header: str) -> str:
    text = unicodedata.normalize("NFKD", header).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().lower()


def _parse_volume_header(header: str) -> tuple[Product, str, bool] | None:
    """Return (product, unit, is_daily_rate) for a volume column, else None."""
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
            return product, unit, is_rate
    return None


def _period(frame: pd.DataFrame, columns: dict[str, str]) -> pd.Series:
    if "periodo" in columns:
        return pd.to_datetime(frame[columns["periodo"]].astype(str).str.strip(), format="%Y-%m")
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

    def parse(self, raw: base.RawFile) -> pd.DataFrame:
        frame = pd.read_csv(raw, sep=";", decimal=",", encoding="utf-8-sig")
        normalized = {_normalize(column): column for column in frame.columns}

        meta: dict[str, pd.Series] = {}
        for alias, role in _META_ALIASES.items():
            if alias in normalized and role not in meta:
                meta[role] = frame[normalized[alias]]
        if "entity" not in meta:
            raise ValueError("ANP file has no well name column ('Poço')")

        period = _period(frame, normalized)
        days_in_month = period.dt.days_in_month

        volumes: dict[Product, pd.Series] = {}
        for column in frame.columns:
            parsed = _parse_volume_header(column)
            if parsed is None:
                continue
            product, unit, is_rate = parsed
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
