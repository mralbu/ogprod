import pytest

import ogprod


def test_builtin_sources_registered():
    assert ogprod.available() == ["anp", "argentina", "sodir"]


def test_get_returns_source_instance():
    source = ogprod.get("sodir")
    assert source.name == "sodir"
    assert source.entity_type == ogprod.EntityType.FIELD


def test_unknown_source():
    with pytest.raises(KeyError, match="unknown source"):
        ogprod.get("opec")


def test_fetch_not_implemented_mentions_docs_url():
    with pytest.raises(NotImplementedError, match="https://"):
        ogprod.get("anp").fetch("/tmp")
