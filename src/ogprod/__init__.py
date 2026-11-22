"""Public oil & gas production data pipeline and geospatial store."""

from importlib.metadata import version

from .core import greet

__version__ = version("ogprod")
__all__ = ["__version__", "greet"]
