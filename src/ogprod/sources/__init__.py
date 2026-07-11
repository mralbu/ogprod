"""Source connectors. Importing this package registers all built-in connectors."""

from ogprod.sources import anp, argentina, sodir  # noqa: F401  (import registers)
from ogprod.sources.base import (
    Source,
    available,
    build_entities,
    build_frame,
    fetch,
    fetch_entities,
    get,
    load,
    load_entities,
    register,
)

__all__ = [
    "Source",
    "available",
    "build_entities",
    "build_frame",
    "fetch",
    "fetch_entities",
    "get",
    "load",
    "load_entities",
    "register",
]
