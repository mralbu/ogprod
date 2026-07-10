"""ogprod — organize public oil & gas production data.

Normalizes well- and field-level production published by regulators worldwide
(ANP, Argentina's Secretaría de Energía, Norway's Sodir, ...) into one
canonical long-format DataFrame.
"""

from ogprod.schema import (
    CANONICAL_COLUMNS,
    CANONICAL_UNIT,
    EntityType,
    Product,
    SchemaError,
    canonical_id,
    validate,
)
from ogprod.sources import available, get, load
from ogprod.units import convert, to_m3

__version__ = "0.1.0"

__all__ = [
    "CANONICAL_COLUMNS",
    "CANONICAL_UNIT",
    "EntityType",
    "Product",
    "SchemaError",
    "available",
    "canonical_id",
    "convert",
    "get",
    "load",
    "to_m3",
    "validate",
]
