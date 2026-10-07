"""Volume unit conversion.

Jurisdictions report in different units: Brazil and Argentina in cubic metres
(gas in thousands of m3), Norway in millions/billions of standard m3, the US
and Canada in barrels and cubic feet. The canonical unit everywhere in ogprod
is the cubic metre; this module converts between the units that show up in
source files.

Unit names are lowercase ASCII identifiers. Multiples of the cubic metre use
an ``e<power>`` prefix (``e3m3`` is a thousand m3 — written "Mm3" by ANP and
"miles de m3" by Argentina's Secretaria de Energia) to avoid the ambiguity of
"M" (thousand in oilfield usage, million in SI).
"""

from __future__ import annotations

_BBL = 0.158987294928  # ISO barrel, exact
_SCF = 0.028316846592  # standard cubic foot, exact

CUBIC_METRES_PER: dict[str, float] = {
    "m3": 1.0,
    "e3m3": 1e3,
    "e6m3": 1e6,
    "e9m3": 1e9,
    "bbl": _BBL,
    "mbbl": _BBL * 1e3,
    "mmbbl": _BBL * 1e6,
    "scf": _SCF,
    "mcf": _SCF * 1e3,
    "mmcf": _SCF * 1e6,
    "bcf": _SCF * 1e9,
}


class UnitError(ValueError):
    """Raised for unknown unit names."""


def factor(unit: str) -> float:
    """Cubic metres per one ``unit``; multiply a whole column by it to convert."""
    try:
        return CUBIC_METRES_PER[unit]
    except KeyError:
        known = ", ".join(sorted(CUBIC_METRES_PER))
        raise UnitError(f"unknown unit {unit!r}; known units: {known}") from None


def convert(value: float, from_unit: str, to_unit: str = "m3") -> float:
    """Convert ``value`` between volume units."""
    return value * factor(from_unit) / factor(to_unit)


def to_m3(value: float, unit: str) -> float:
    """Convert ``value`` in ``unit`` to cubic metres."""
    return value * factor(unit)
