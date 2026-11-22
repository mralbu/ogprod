"""Registered public oil & gas data connectors."""

from __future__ import annotations

# Imports drive connector registration via @base.register.
from ogprod.sources import anp as anp
from ogprod.sources import mexico as mexico
from ogprod.sources import sodir as sodir
from ogprod.sources.base import DownloadResult, RemoteFile, Source, available, get, register

__all__ = [
    "DownloadResult",
    "RemoteFile",
    "Source",
    "anp",
    "available",
    "get",
    "mexico",
    "register",
    "sodir",
]
