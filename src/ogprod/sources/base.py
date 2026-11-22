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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, ClassVar, TypeVar

import geopandas as gpd
import httpx2
import pandas as pd

from ogprod import schema

RawFile = str | Path | IO[str] | IO[bytes]

_USER_AGENT = "ogprod/0.2"
_TIMEOUT = httpx2.Timeout(120.0)


@contextmanager
def open_client(client: httpx2.Client | None) -> Iterator[httpx2.Client]:
    """Yield ``client`` as-is, or a temporary one with ogprod defaults."""
    if client is not None:
        yield client
        return
    with httpx2.Client(
        follow_redirects=True, timeout=_TIMEOUT, headers={"User-Agent": _USER_AGENT}
    ) as owned:
        yield owned


def download(client: httpx2.Client, url: str, dest: Path) -> Path:
    """Stream ``url`` into ``dest`` and return ``dest``."""
    with client.stream("GET", url) as response:
        response.raise_for_status()
        with dest.open("wb") as sink:
            for chunk in response.iter_bytes():
                sink.write(chunk)
    return dest


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

    def fetch(self, dest_dir: str | Path, *, client: httpx2.Client | None = None) -> list[Path]:
        """Download raw file(s) into ``dest_dir`` and return their paths.

        Returns a list because some publishers split one period across several
        files (ANP's terra/mar/pré-sal). Each returned path can be passed to
        ``parse``. Connectors without a stable download URL leave this
        unimplemented and document where to fetch files manually; ``client``
        lets callers inject a configured ``httpx2.Client`` (tests use a mock
        transport). Overrides declare their options (``year``, ``month``) as
        explicit keywords with no ``**kwargs`` catch-all, so a misspelt option
        raises ``TypeError`` instead of silently widening the download.
        """
        raise NotImplementedError(f"{self.name} has no automated fetch; see {self.docs_url}")

    def restatement_order(self, paths: list[Path]) -> list[Path]:
        """Order raw files so that a file restating another's rows comes after it.

        Ingest parses files in this order and keeps the last row per key, so a
        publisher whose files overlap (ANP's pré-sal file restates a subset of
        the mar file) overrides this to make its rule explicit. The default
        keeps the given order.
        """
        return list(paths)

    def parse_entities(self, raw: RawFile) -> gpd.GeoDataFrame:
        """Parse a raw publisher file into the canonical entities GeoDataFrame.

        One row per entity: well coordinates as Points, field ring fences as
        (Multi)Polygons, in EPSG:4326. Connectors whose publisher has no
        geometry dataset leave this unimplemented.
        """
        raise NotImplementedError(f"{self.name} has no entity parser; see {self.docs_url}")

    def fetch_entities(
        self, dest_dir: str | Path, *, client: httpx2.Client | None = None
    ) -> list[Path]:
        """Download the publisher's entity/geometry file(s) into ``dest_dir``."""
        raise NotImplementedError(f"{self.name} has no automated entity fetch; see {self.docs_url}")


_REGISTRY: dict[str, type[Source]] = {}
_S = TypeVar("_S", bound=Source)


def register(cls: type[_S]) -> type[_S]:
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
    does not report simply does not appear), and so are rows with a missing
    entity id or period — publisher dirt that can't be keyed in the store.
    """
    na = pd.Series(pd.NA, index=entity_id.index, dtype="string")
    meta = pd.DataFrame(
        {
            "source": source,
            "entity_type": entity_type.value,
            "entity_id": id_string(entity_id),
            "entity_name": entity_name.astype("string"),
            "field": na if field is None else field.astype("string"),
            "basin": na if basin is None else basin.astype("string"),
            "region": na if region is None else region.astype("string"),
            "operator": na if operator is None else operator.astype("string"),
            "period": pd.to_datetime(period),
        }
    ).dropna(subset=["entity_id", "period"])
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


def id_string(series: pd.Series) -> pd.Series:
    """Render an entity-id column as clean strings.

    Numeric id columns (Sodir NPDIDs, Argentina ``idpozo``) arrive
    float-typed whenever a null elsewhere promotes them, and a bare
    ``.astype("string")`` on float64 yields "50136.0" instead of "50136" —
    silently breaking the (source, entity_id) join between production and
    entities. Integral float columns are routed through nullable Int64;
    anything else (including string ids with leading zeros) passes through
    unchanged.
    """
    if pd.api.types.is_float_dtype(series) and (series.dropna() % 1 == 0).all():
        return series.astype("Int64").astype("string")
    return series.astype("string")


def build_entities(
    *,
    source: str,
    entity_type: schema.EntityType,
    entity_id: pd.Series,
    entity_name: pd.Series,
    geometry: gpd.GeoSeries | list,
    field: pd.Series | None = None,
    basin: pd.Series | None = None,
    region: pd.Series | None = None,
    operator: pd.Series | None = None,
    status: pd.Series | None = None,
    crs: Any = schema.ENTITY_CRS,
) -> gpd.GeoDataFrame:
    """Assemble a canonical entities frame; reprojects to EPSG:4326 and validates.

    All series must share an index. ``crs`` describes the *incoming* geometry;
    pass the publisher's CRS and the frame is reprojected. Rows with a
    missing entity id are dropped (they can't join anything); rows with
    missing geometry are kept — an entity without coordinates still carries
    joinable metadata.
    """
    na = pd.Series(pd.NA, index=entity_id.index, dtype="string")

    def text(series: pd.Series | None) -> pd.Series:
        return na if series is None else series.astype("string")

    entities = gpd.GeoDataFrame(
        {
            "source": source,
            "entity_type": entity_type.value,
            "entity_id": id_string(entity_id),
            "entity_name": entity_name.astype("string"),
            "field": text(field),
            "basin": text(basin),
            "region": text(region),
            "operator": text(operator),
            "status": text(status),
            "geometry": geometry,
        },
        geometry="geometry",
        crs=crs,
    )
    if entities.crs is None or entities.crs.to_epsg() != 4326:
        entities = entities.to_crs(schema.ENTITY_CRS)
    entities = entities[entities["entity_id"].notna()]
    entities = entities[list(schema.ENTITY_COLUMNS)].reset_index(drop=True)
    return schema.validate_entities(entities)
