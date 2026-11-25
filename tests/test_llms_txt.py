"""Property and unit tests for ogprod.llms_txt."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ogprod import llms_txt, schema

_HOST_CHARS = st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-")
_PATH_CHARS = st.characters(whitelist_categories=("Ll", "Nd"), whitelist_characters="-_")


@st.composite
def _http_urls(draw: st.DrawFn) -> str:
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.text(_HOST_CHARS, min_size=1, max_size=12))
    segments = draw(st.lists(st.text(_PATH_CHARS, min_size=1, max_size=8), max_size=3))
    trailing = "/" * draw(st.integers(min_value=0, max_value=3))
    lead_ws = " " * draw(st.integers(min_value=0, max_value=2))
    tail_ws = " " * draw(st.integers(min_value=0, max_value=2))
    path = "/" + "/".join(segments) if segments else ""
    return f"{lead_ws}{scheme}://{host}.example{path}{trailing}{tail_ws}"


@given(url=_http_urls())
def test_normalize_site_url_idempotent_and_single_trailing_slash(url: str) -> None:
    norm = llms_txt.normalize_site_url(url)
    assert norm.startswith(("http://", "https://"))
    assert norm.endswith("/")
    assert not norm.endswith("//")
    assert llms_txt.normalize_site_url(norm) == norm


@pytest.mark.parametrize("bad", ["example.com", "ftp://example.com", "/ogprod/", "javascript:x"])
def test_normalize_site_url_rejects_invalid_scheme(bad: str) -> None:
    with pytest.raises(ValueError, match="http:// or https://"):
        llms_txt.normalize_site_url(bad)


def test_normalize_site_url_blank_returns_empty() -> None:
    assert llms_txt.normalize_site_url(None) == ""
    assert llms_txt.normalize_site_url("") == ""
    assert llms_txt.normalize_site_url("   ") == ""


@given(num_bytes=st.integers(min_value=0, max_value=500 * 1024 * 1024))
def test_format_byte_size_matches_magnitude(num_bytes: int) -> None:
    formatted = llms_txt.format_byte_size(num_bytes)
    match = re.fullmatch(r"(\d+(?:\.\d)?) (B|KB|MB)", formatted)
    assert match is not None, formatted
    value = float(match.group(1))
    unit = match.group(2)
    if num_bytes < 1024:
        assert unit == "B"
        assert int(value) == num_bytes
    elif num_bytes < 1024 * 1024:
        assert unit == "KB"
        assert abs(value * 1024 - num_bytes) <= 60
    else:
        assert unit == "MB"
        assert abs(value * 1024 * 1024 - num_bytes) <= 60_000


def test_format_byte_size_rejects_negative() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        llms_txt.format_byte_size(-1)


@given(
    source_ids=st.lists(
        st.text(st.characters(whitelist_categories=("Ll",)), min_size=2, max_size=8),
        min_size=0,
        max_size=4,
        unique=True,
    ),
    site_url=st.one_of(st.none(), _http_urls()),
)
def test_render_llms_txt_structural_invariants(source_ids: list[str], site_url: str | None) -> None:
    meta: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": "2026-01-15T12:00:00Z",
        "mirror": {
            "repo": "mralbu/ogprod",
            "revision": "main",
            "base_url": "https://huggingface.co/datasets/mralbu/ogprod/resolve/main",
        },
        "units": dict(schema.RATE_UNITS),
        "has_tiles": False,
        "sources": [
            {
                "id": sid,
                "country": sid.title(),
                "publisher": f"{sid.upper()} Reg",
                "entity_type": "field",
                "years": [2024, 2025],
                "first_period": "2024-01-01",
                "last_period": "2025-12-01",
                "counts": {
                    "production_rows": 120,
                    "producing_entities": 5,
                    "fields": 5,
                    "facilities": 2,
                    "wells": 10,
                },
                "latest_oil_bpd": 10_000.0,
                "latest_gas_e3m3d": 250.5,
                "available_products": ["oil_prod", "gas_prod"],
                "known_gaps": [],
            }
            for sid in source_ids
        ],
    }

    text = llms_txt.render_llms_txt(meta, site_url=site_url)
    expected_base = llms_txt.normalize_site_url(site_url)

    assert text.startswith("# ogprod")
    assert "\n> " in text
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "`bbl/d`" in text
    assert "`e3m3/d`" in text
    assert f"[meta.json]({expected_base}data/meta.json)" in text
    assert (
        f"[rollups/country_monthly.json]({expected_base}data/rollups/country_monthly.json)" in text
    )
    assert f"{expected_base}?source=" in text
    assert "gor,water_cut" in text
    for sid in source_ids:
        assert f"`{sid}`" in text
        assert f"rollups/field_monthly/{sid}.json" in text


def test_collect_asset_sizes_walks_files_only(tmp_path: Path) -> None:
    assert llms_txt.collect_asset_sizes(tmp_path / "missing") == {}

    (tmp_path / "meta.json").write_bytes(b"12345")
    (tmp_path / "rollups").mkdir()
    (tmp_path / "rollups" / "country_monthly.json").write_bytes(b"123")
    assert llms_txt.collect_asset_sizes(tmp_path) == {
        "meta.json": 5,
        "rollups/country_monthly.json": 3,
    }
