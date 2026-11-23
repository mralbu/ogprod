"""Registered public oil & gas data connectors."""

from __future__ import annotations

# Imports drive connector registration via @base.register (see docs/adr/0007).
from ogprod.sources import brazil as brazil
from ogprod.sources import mexico as mexico
from ogprod.sources import norway as norway
from ogprod.sources.base import DownloadResult, RemoteFile, Source, available, get, register

__all__ = [
    "DownloadResult",
    "RemoteFile",
    "Source",
    "available",
    "brazil",
    "get",
    "mexico",
    "norway",
    "register",
]
