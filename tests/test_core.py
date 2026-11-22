import pytest
from hypothesis import given
from hypothesis import strategies as st

import ogprod
from ogprod import greet


@pytest.mark.parametrize(
    ("name", "expected"),
    [("world", "Hello, world!"), ("Ada", "Hello, Ada!")],
)
def test_greet(name: str, expected: str) -> None:
    assert greet(name) == expected


@given(st.text())
def test_greet_wraps_any_name(name: str) -> None:
    greeting = greet(name)
    assert greeting.startswith("Hello, ")
    assert greeting.endswith("!")
    assert greeting.removeprefix("Hello, ").removesuffix("!") == name


def test_version() -> None:
    assert ogprod.__version__
