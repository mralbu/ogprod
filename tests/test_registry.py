import pytest

import ogprod
from ogprod.sources import base


def test_builtin_sources_registered():
    assert ogprod.available() == ["anp", "argentina", "sodir"]


def test_get_returns_source_instance():
    source = ogprod.get("sodir")
    assert source.name == "sodir"
    assert source.entity_type == ogprod.EntityType.FIELD


def test_unknown_source():
    with pytest.raises(KeyError, match="unknown source"):
        ogprod.get("opec")


def test_fetch_default_not_implemented_mentions_docs_url():
    class NoFetch(base.Source):
        name = "nofetch"
        entity_type = ogprod.EntityType.WELL
        docs_url = "https://example.org/data"

        def parse(self, raw):
            raise NotImplementedError

    with pytest.raises(NotImplementedError, match="https://example.org/data"):
        NoFetch().fetch("/tmp")
