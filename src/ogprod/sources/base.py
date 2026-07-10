"""Source connector interface and registry.

A connector maps one publisher's raw files into the canonical frame defined in
:mod:`ogprod.schema`. Connectors separate *fetching* (network, optional —
``fetch`` downloads a raw file and returns its path) from *parsing* (pure,
always available — ``parse`` turns a raw file into a canonical DataFrame), so
parsing stays testable offline and files downloaded by other means can be
loaded directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, ClassVar

import pandas as pd

from ogprod import schema

RawFile = str | Path | IO[str] | IO[bytes]


class Source(ABC):
    """A single publisher of production data (e.g. ANP, Sodir)."""

    #: Registry key, e.g. ``"anp"``.
    name: ClassVar[str]
    #: Granularity of the data this source publishes.
    entity_type: ClassVar[schema.EntityType]
    #: Where the data and its documentation live.
    docs_url: ClassVar[str]

    @abstractmethod
    def parse(self, raw: RawFile) -> pd.DataFrame:
        """Parse a raw file from this source into a canonical DataFrame."""

    def fetch(self, dest_dir: str | Path, **kwargs: object) -> Path:
        """Download a raw file into ``dest_dir`` and return its path.

        Optional: connectors without a stable download URL only implement
        ``parse`` and document where to fetch files manually.
        """
        raise NotImplementedError(f"{self.name} has no automated fetch; see {self.docs_url}")


_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator adding a connector to the registry."""
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Source:
    """Instantiate the connector registered under ``name``."""
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise KeyError(f"unknown source {name!r}; available: {available()}") from None


def available() -> list[str]:
    """Names of all registered connectors."""
    return sorted(_REGISTRY)


def load(name: str, raw: RawFile) -> pd.DataFrame:
    """Parse ``raw`` with the connector registered under ``name``."""
    return get(name).parse(raw)


def build_frame(
    *,
    source: str,
    entity_type: schema.EntityType,
    entity_id: pd.Series,
    entity_name: pd.Series,
    period: pd.Series,
    volumes_m3: dict[schema.Product, pd.Series],
    field: pd.Series | None = None,
    basin: pd.Series | None = None,
    region: pd.Series | None = None,
    operator: pd.Series | None = None,
) -> pd.DataFrame:
    """Assemble a canonical long frame from per-product volume columns.

    All series must share an index; ``volumes_m3`` values must already be in
    cubic metres. Rows with missing volume are dropped (a product a source
    does not report simply does not appear).
    """
    na = pd.Series(pd.NA, index=entity_id.index, dtype="string")
    meta = pd.DataFrame(
        {
            "source": source,
            "entity_type": entity_type.value,
            "entity_id": entity_id.astype("string"),
            "entity_name": entity_name.astype("string"),
            "field": na if field is None else field.astype("string"),
            "basin": na if basin is None else basin.astype("string"),
            "region": na if region is None else region.astype("string"),
            "operator": na if operator is None else operator.astype("string"),
            "period": pd.to_datetime(period),
        }
    )
    parts = []
    for product, volume in volumes_m3.items():
        part = meta.copy()
        part["product"] = product.value
        part["volume"] = pd.to_numeric(volume, errors="coerce")
        parts.append(part.dropna(subset=["volume"]))
    if not parts:
        return schema.empty_frame()
    frame = pd.concat(parts, ignore_index=True)[list(schema.CANONICAL_COLUMNS)]
    return schema.validate(frame)
