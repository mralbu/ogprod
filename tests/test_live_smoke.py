"""Live smoke tests — one small fetch + parse per source.

Every publisher has already moved off its documented endpoint at least once;
these tests exist to notice the next drift before a user does. They hit the
real portals, so they are deselected by default (``addopts = -m 'not live'``)
and run weekly in CI (``.github/workflows/smoke.yml``) or by hand with
``just test -m live``.

Downloads are kept small: sodir's whole production history is ~2 MB, one ANP
month is a few MB, and Argentina is covered through its wells listing (the
production files are 100-300 MB per year, too heavy for a weekly job).
"""

import pytest

import ogprod

pytestmark = pytest.mark.live


def test_sodir_production_fetch_and_parse(tmp_path):
    paths = ogprod.fetch("sodir", tmp_path)
    frame = ogprod.load("sodir", paths[0])
    assert len(frame) > 100_000  # whole history back to 1971


def test_sodir_entities_fetch_and_parse(tmp_path):
    paths = ogprod.fetch_entities("sodir", tmp_path)
    entities = ogprod.load_entities("sodir", paths[0])
    assert len(entities) > 100  # 142 fields as of July 2026
    assert entities.geometry.notna().sum() > 100


def test_anp_production_fetch_and_parse(tmp_path):
    paths = ogprod.fetch("anp", tmp_path, year=2023, month=6)
    frames = [ogprod.load("anp", path) for path in paths]
    assert sum(len(frame) for frame in frames) > 5_000  # ~7k producing wells


def test_anp_entities_fetch_and_parse(tmp_path):
    paths = ogprod.fetch_entities("anp", tmp_path)
    entities = ogprod.load_entities("anp", paths[0])
    assert len(entities) > 25_000  # 31k-well cadastre as of July 2026


def test_argentina_entities_fetch_and_parse(tmp_path):
    paths = ogprod.fetch_entities("argentina", tmp_path)
    entities = ogprod.load_entities("argentina", paths[0])
    assert len(entities) > 50_000  # 84k wells as of July 2026
