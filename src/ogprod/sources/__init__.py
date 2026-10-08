"""Source connectors. Importing this package registers all built-in connectors.

This is the connector layer: ``get(name)`` returns a :class:`Source` whose
``fetch``/``parse`` work on single raw files (useful for diagnostics or files
downloaded by other means). Everyday use goes through :class:`ogprod.Store`.
"""

from ogprod.sources import anp, argentina, sodir  # noqa: F401  (import registers)
from ogprod.sources.base import (
    Source,
    available,
    build_entities,
    build_frame,
    get,
    register,
)

__all__ = [
    "Source",
    "available",
    "build_entities",
    "build_frame",
    "get",
    "register",
]
