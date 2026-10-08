"""ogprod — organize public oil & gas production data.

Normalizes well- and field-level production published by regulators worldwide
(ANP, Argentina's Secretaría de Energía, Norway's Sodir, ...) into one
canonical long-format table, kept in a local Parquet store::

    import ogprod

    db = ogprod.Store()
    db.ingest("sodir")
    oil = db.production(source="sodir", product="oil", start="2020-01")

Connector internals live in :mod:`ogprod.sources`, the canonical schema in
:mod:`ogprod.schema`.
"""

from ogprod.schema import Product, SchemaError
from ogprod.sources import available
from ogprod.store import Store
from ogprod.units import convert

__version__ = "0.2.0"

__all__ = ["Product", "SchemaError", "Store", "available", "convert"]
