"""Tests for ogprod.units (exact ISO conversions and Hypothesis round-trips)."""

from __future__ import annotations

import math
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ogprod import units

UNIT_NAMES = sorted(units.VOLUME_TO_M3)


@given(
    value=st.floats(min_value=-1e9, max_value=1e9, allow_nan=False, allow_infinity=False),
    u1=st.sampled_from(UNIT_NAMES),
    u2=st.sampled_from(UNIT_NAMES),
)
def test_convert_volume_roundtrip(value: float, u1: str, u2: str) -> None:
    converted = units.convert_volume(value, u1, u2)
    restored = units.convert_volume(converted, u2, u1)
    assert math.isclose(restored, value, rel_tol=1e-9, abs_tol=1e-9)


@given(
    volume=st.floats(min_value=0.0, max_value=1e9, allow_nan=False, allow_infinity=False),
    year=st.integers(min_value=1940, max_value=2100),
    month=st.integers(min_value=1, max_value=12),
)
def test_monthly_volume_to_rate_inverts_days(volume: float, year: int, month: int) -> None:
    days = units.days_in_month(year, month)
    rate_bpd = units.monthly_volume_to_rate(volume, days, "m3", units.LIQUID_RATE_UNIT)
    expected = (volume * units.BBL_PER_M3) / days
    assert math.isclose(rate_bpd, expected, rel_tol=1e-12, abs_tol=1e-12)


def test_days_in_month_leap_years() -> None:
    assert units.days_in_month(2024, 2) == 29
    assert units.days_in_month(2025, 2) == 28
    assert units.days_in_month(2000, 2) == 29
    assert units.days_in_month(1900, 2) == 28
    assert units.days_in_period(date(2026, 1, 1)) == 31


def test_invalid_units_and_days_raise() -> None:
    with pytest.raises(ValueError, match="unknown volume unit"):
        units.volume_factor("gallons", "m3")
    with pytest.raises(ValueError, match="unknown volume unit"):
        units.volume_factor("m3", "liters")
    with pytest.raises(ValueError, match="month must be"):
        units.days_in_month(2026, 13)
    with pytest.raises(ValueError, match="days must be positive"):
        units.monthly_volume_to_rate(100.0, 0, "m3", "bbl/d")
    with pytest.raises(ValueError, match="rate unit"):
        units.monthly_volume_to_rate(100.0, 30, "m3", "bbl")
