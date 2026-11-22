"""Volume and daily-rate unit conversions.

Canonical storage units in ogprod are calendar-day average rates:

- Liquids (oil, condensate, NGL, water produced & injected): ``bbl/d``
- Gases (total gas produced, gas injected, CO₂ injected): ``e3m3/d`` (10³ m³/d, thousand m³/day)

Unit names use explicit ``e<power>`` prefixes (``e3m3``, ``e6m3``, ``e9m3``) to
avoid both the oilfield vs SI ambiguity of "M" (ANP writes ``Mm³`` for *mil* =
10³ m³, whereas SI ``Mm³`` is 10⁶ m³) and ``km³`` (which in SI means cubic
kilometer = 10⁹ m³).
"""

from __future__ import annotations

import calendar
from datetime import date

#: Exact ISO cubic metres per barrel.
M3_PER_BBL: float = 0.158987294928

#: Exact ISO barrels per cubic metre.
BBL_PER_M3: float = 1.0 / M3_PER_BBL

#: Exact ISO cubic metres per standard cubic foot.
M3_PER_SCF: float = 0.028316846592

#: Exact cubic metres per unit for every supported volume unit.
VOLUME_TO_M3: dict[str, float] = {
    "m3": 1.0,
    "e3m3": 1_000.0,
    "e6m3": 1_000_000.0,
    "e9m3": 1_000_000_000.0,
    "bbl": M3_PER_BBL,
    "mbbl": 1_000.0 * M3_PER_BBL,
    "mmbbl": 1_000_000.0 * M3_PER_BBL,
    "scf": M3_PER_SCF,
    "mcf": 1_000.0 * M3_PER_SCF,
    "mmcf": 1_000_000.0 * M3_PER_SCF,
    "bcf": 1_000_000_000.0 * M3_PER_SCF,
}

#: Canonical rate units for liquids and gases.
LIQUID_RATE_UNIT = "bbl/d"
GAS_RATE_UNIT = "e3m3/d"


def volume_factor(from_unit: str, to_unit: str = "m3") -> float:
    """Return the multiplicative factor to convert ``from_unit`` to ``to_unit``."""
    try:
        src = VOLUME_TO_M3[from_unit]
    except KeyError as err:
        raise ValueError(f"unknown volume unit: {from_unit!r}") from err
    try:
        dst = VOLUME_TO_M3[to_unit]
    except KeyError as err:
        raise ValueError(f"unknown volume unit: {to_unit!r}") from err
    return src / dst


def convert_volume(value: float, from_unit: str, to_unit: str) -> float:
    """Convert a scalar volume from ``from_unit`` to ``to_unit``."""
    return value * volume_factor(from_unit, to_unit)


def days_in_month(year: int, month: int) -> int:
    """Return the exact number of calendar days in ``(year, month)``."""
    if not 1 <= month <= 12:
        raise ValueError(f"month must be in 1..12, got {month}")
    return calendar.monthrange(year, month)[1]


def days_in_period(period: date) -> int:
    """Return the number of calendar days in the month of ``period``."""
    return days_in_month(period.year, period.month)


def monthly_volume_to_rate(
    volume: float,
    days: int,
    from_unit: str,
    to_rate_unit: str,
) -> float:
    """Convert a monthly total volume into a calendar-day average rate.

    ``to_rate_unit`` must be ``"<volume_unit>/d"`` (e.g. ``"bbl/d"``, ``"e6m3/d"``).
    """
    if days <= 0:
        raise ValueError(f"days must be positive, got {days}")
    if not to_rate_unit.endswith("/d"):
        raise ValueError(f"rate unit must end with '/d', got {to_rate_unit!r}")
    target_volume_unit = to_rate_unit.removesuffix("/d")
    return convert_volume(volume, from_unit, target_volume_unit) / days
