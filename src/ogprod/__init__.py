"""Public oil & gas production data pipeline and geospatial store."""

from importlib.metadata import version

from ogprod.schema import RATE_UNITS, EntityType, GeoKind, SchemaError
from ogprod.sources import available
from ogprod.store import IngestSummary, Store
from ogprod.units import convert_volume, monthly_volume_to_rate

__version__ = version("ogprod")
__all__ = [
    "RATE_UNITS",
    "EntityType",
    "GeoKind",
    "IngestSummary",
    "SchemaError",
    "Store",
    "__version__",
    "available",
    "convert_volume",
    "monthly_volume_to_rate",
]
