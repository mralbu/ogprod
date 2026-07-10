"""Source connectors. Importing this package registers all built-in connectors."""

from ogprod.sources import anp, argentina, sodir  # noqa: F401  (import registers)
from ogprod.sources.base import Source, available, build_frame, fetch, get, load, register

__all__ = ["Source", "available", "build_frame", "fetch", "get", "load", "register"]
