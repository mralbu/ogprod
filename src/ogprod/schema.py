"""Canonical data model.

Every source is normalized into a long-format :class:`pandas.DataFrame` with
one row per ``(entity, period, product)``. Volumes are always stored in cubic
metres (see :mod:`ogprod.units`); identifiers stay source-native, with
:func:`canonical_id` providing a namespaced global identifier.
"""

from __future__ import annotations

import enum

import pandas as pd


class EntityType(enum.StrEnum):
    WELL = "well"
    FIELD = "field"


class Product(enum.StrEnum):
    OIL = "oil"
    GAS = "gas"
    CONDENSATE = "condensate"
    NGL = "ngl"
    WATER = "water"


#: All volumes in a canonical frame are expressed in this unit.
CANONICAL_UNIT = "m3"

CANONICAL_COLUMNS: tuple[str, ...] = (
    "source",  # connector name, e.g. "anp"
    "entity_type",  # "well" | "field"
    "entity_id",  # source-native identifier (ANP well code, NPDID, ...)
    "entity_name",  # human-readable name
    "field",  # field the entity belongs to (== entity_name for fields)
    "basin",
    "region",  # state / province / offshore area
    "operator",
    "period",  # first day of the production month (datetime64[ns])
    "product",  # a Product value
    "volume",  # volume produced over the period, in CANONICAL_UNIT
)

_STRING_COLUMNS = (
    "source",
    "entity_type",
    "entity_id",
    "entity_name",
    "field",
    "basin",
    "region",
    "operator",
    "product",
)


class SchemaError(ValueError):
    """Raised when a DataFrame does not conform to the canonical schema."""


def canonical_id(source: str, entity_id: str) -> str:
    """Namespaced identifier that is unique across sources."""
    return f"{source}:{entity_id}"


def month_start(year: pd.Series, month: pd.Series) -> pd.Series:
    """Build canonical month-start timestamps from year and month columns."""
    parts = pd.DataFrame(
        {
            "year": pd.to_numeric(year, errors="raise"),
            "month": pd.to_numeric(month, errors="raise"),
            "day": 1,
        }
    )
    return pd.to_datetime(parts)


def empty_frame() -> pd.DataFrame:
    """An empty DataFrame with the canonical columns and dtypes."""
    frame = pd.DataFrame({column: pd.Series(dtype="string") for column in _STRING_COLUMNS})
    frame["period"] = pd.Series(dtype="datetime64[ns]")
    frame["volume"] = pd.Series(dtype="float64")
    return frame[list(CANONICAL_COLUMNS)]


def validate(frame: pd.DataFrame) -> pd.DataFrame:
    """Check that ``frame`` conforms to the canonical schema.

    Returns the frame unchanged so calls can be inlined; raises
    :class:`SchemaError` describing the first problem found.
    """
    missing = [column for column in CANONICAL_COLUMNS if column not in frame.columns]
    if missing:
        raise SchemaError(f"missing canonical columns: {missing}")

    if len(frame) == 0:
        return frame

    bad_types = set(frame["entity_type"].dropna().unique()) - {e.value for e in EntityType}
    if bad_types:
        raise SchemaError(f"invalid entity_type values: {sorted(bad_types)}")

    bad_products = set(frame["product"].dropna().unique()) - {p.value for p in Product}
    if bad_products:
        raise SchemaError(f"invalid product values: {sorted(bad_products)}")

    if not pd.api.types.is_datetime64_any_dtype(frame["period"]):
        raise SchemaError("'period' must be a datetime column")
    periods = frame["period"].dropna()
    if not ((periods.dt.day == 1).all() and (periods.dt.normalize() == periods).all()):
        raise SchemaError("'period' values must be normalized to the first day of the month")

    if not pd.api.types.is_numeric_dtype(frame["volume"]):
        raise SchemaError("'volume' must be numeric")

    return frame
