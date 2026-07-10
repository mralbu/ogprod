import pytest

from ogprod import units


def test_identity():
    assert units.convert(42.0, "m3", "m3") == 42.0


def test_barrel_factor():
    assert units.to_m3(1.0, "bbl") == pytest.approx(0.158987294928)


def test_thousand_cubic_metres():
    assert units.to_m3(2.5, "e3m3") == pytest.approx(2500.0)


def test_gas_units():
    assert units.convert(1.0, "bcf", "e6m3") == pytest.approx(28.316846592)


def test_round_trip():
    assert units.convert(units.convert(123.4, "bbl", "m3"), "m3", "bbl") == pytest.approx(123.4)


@pytest.mark.parametrize("bad", ["barrels", "M3", "", "litres"])
def test_unknown_unit_raises(bad):
    with pytest.raises(units.UnitError, match="unknown unit"):
        units.to_m3(1.0, bad)
