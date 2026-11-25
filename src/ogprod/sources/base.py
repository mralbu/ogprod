"""Base connector protocol, source registry, HTTP helpers, and table builders."""

from __future__ import annotations

import hashlib
import time
from abc import ABC, abstractmethod
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO

import geopandas as gpd
import httpx2
import numpy as np
import pandas as pd
import pyarrow as pa

from ogprod import schema

type RawFile = str | Path | BinaryIO

#: Descriptive User-Agent required by portals such as gov.br (blocks default python-httpx).
USER_AGENT = "ogprod/0.1 (+https://github.com/mralbu/ogprod)"

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RemoteFile:
    """One upstream file advertised by a regulator portal."""

    url: str
    key: str
    year_min: int | None = None
    year_max: int | None = None


@dataclass(frozen=True)
class KnownGap:
    """A known stretch of months where a source's reporting is incomplete."""

    column: str
    start: date
    end: date
    description: str
    resolved_in_country_rollup: bool = False
    country_rollup_source: str | None = None


@dataclass(frozen=True)
class DownloadResult:
    """Metadata captured while streaming a remote file to disk."""

    path: Path
    url: str
    sha256: str
    size_bytes: int
    etag: str | None = None
    last_modified: str | None = None


@contextmanager
def open_client(client: httpx2.Client | None = None) -> Generator[httpx2.Client]:
    """Yield an injected httpx2.Client, or open a managed one with our User-Agent."""
    if client is not None:
        yield client
        return
    with httpx2.Client(
        timeout=httpx2.Timeout(60.0, connect=20.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as managed:
        yield managed


def _is_retryable_http_error(err: httpx2.HTTPError) -> bool:
    if isinstance(err, httpx2.TransportError):
        return True
    if isinstance(err, httpx2.HTTPStatusError):
        return err.response.status_code in _RETRYABLE_STATUS
    return False


def get_with_retry(
    client: httpx2.Client,
    url: str,
    *,
    retries: int = 3,
    backoff_s: float = 1.0,
) -> httpx2.Response:
    """Perform an HTTP GET with bounded exponential backoff on transient errors / 5xx / 429."""
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp
        except httpx2.HTTPError as err:
            if attempt + 1 >= attempts or not _is_retryable_http_error(err):
                raise
            if backoff_s > 0:
                time.sleep(backoff_s * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def _verify_content_length(headers: httpx2.Headers, actual_bytes: int, url: str) -> None:
    if headers.get("content-encoding"):
        return
    raw_len = headers.get("content-length")
    if raw_len is None or not raw_len.strip().isdigit():
        return
    expected = int(raw_len.strip())
    if actual_bytes != expected:
        raise httpx2.RemoteProtocolError(
            f"truncated download for {url}: expected {expected} bytes, got {actual_bytes}"
        )


def _stream_once(client: httpx2.Client, url: str, dest: Path) -> DownloadResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            headers = resp.headers
            with part.open("wb") as sink:
                for chunk in resp.iter_bytes():
                    sink.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            _verify_content_length(headers, size, url)
        part.replace(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    return DownloadResult(
        path=dest,
        url=url,
        sha256=digest.hexdigest(),
        size_bytes=size,
        etag=headers.get("etag"),
        last_modified=headers.get("last-modified"),
    )


def download_file(
    client: httpx2.Client,
    url: str,
    dest: Path,
    *,
    retries: int = 3,
    backoff_s: float = 1.0,
) -> DownloadResult:
    """Stream ``url`` atomically into ``dest`` with retries and ``Content-Length`` verification."""
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            return _stream_once(client, url, dest)
        except httpx2.HTTPError as err:
            if attempt + 1 >= attempts or not _is_retryable_http_error(err):
                raise
            if backoff_s > 0:
                time.sleep(backoff_s * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover


def _clean_string_series(series: pd.Series | None, length: int, *, fill: str | None) -> pa.Array:
    if series is None:
        return pa.array([fill] * length, type=pa.string())
    text = series.astype("string").str.strip()
    text = text.mask(text == "", pd.NA)
    if fill is not None:
        text = text.fillna(fill)
    return pa.array(text, type=pa.string(), from_pandas=True)


def _clean_rate_series(series: pd.Series | None, length: int) -> pa.Array:
    if series is None:
        return pa.array([None] * length, type=pa.float32())
    numeric = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float32, na_value=np.nan)
    mask = np.isnan(numeric)
    return pa.array(numeric, mask=mask, type=pa.float32())


def build_production_table(
    *,
    source: str,
    entity_type: schema.EntityType,
    entity_id: pd.Series,
    entity_name: pd.Series,
    period: pd.Series,
    rates: Mapping[str, pd.Series],
    field_id: pd.Series | None = None,
    field_name: pd.Series | None = None,
    facility_id: pd.Series | None = None,
    facility_name: pd.Series | None = None,
    basin: pd.Series | None = None,
    region: pd.Series | None = None,
    environment: pd.Series | None = None,
    operator: pd.Series | None = None,
) -> pa.Table:
    """Assemble and validate a canonical wide ``float32`` production Arrow table."""
    n = len(entity_id)
    if n == 0:
        return schema.empty_production()

    valid_id = entity_id.astype("string").str.strip()
    valid_id = valid_id.mask(valid_id == "", pd.NA)
    periods = pd.to_datetime(period, errors="coerce")
    keep = valid_id.notna() & periods.notna()
    if not keep.all():
        return _build_filtered_table(
            keep=keep,
            source=source,
            entity_type=entity_type,
            entity_id=valid_id,
            entity_name=entity_name,
            periods=periods,
            rates=rates,
            field_id=field_id,
            field_name=field_name,
            facility_id=facility_id,
            facility_name=facility_name,
            basin=basin,
            region=region,
            environment=environment,
            operator=operator,
        )
    return _assemble_arrow_table(
        source=source,
        entity_type=entity_type,
        entity_id=valid_id,
        entity_name=entity_name,
        periods=periods,
        rates=rates,
        field_id=field_id,
        field_name=field_name,
        facility_id=facility_id,
        facility_name=facility_name,
        basin=basin,
        region=region,
        environment=environment,
        operator=operator,
    )


def _slice_opt(series: pd.Series | None, mask: pd.Series) -> pd.Series | None:
    return None if series is None else series.loc[mask].reset_index(drop=True)


def _build_filtered_table(
    *,
    keep: pd.Series,
    source: str,
    entity_type: schema.EntityType,
    entity_id: pd.Series,
    entity_name: pd.Series,
    periods: pd.Series,
    rates: Mapping[str, pd.Series],
    field_id: pd.Series | None,
    field_name: pd.Series | None,
    facility_id: pd.Series | None,
    facility_name: pd.Series | None,
    basin: pd.Series | None,
    region: pd.Series | None,
    environment: pd.Series | None,
    operator: pd.Series | None,
) -> pa.Table:
    if not keep.any():
        return schema.empty_production()
    return _assemble_arrow_table(
        source=source,
        entity_type=entity_type,
        entity_id=entity_id.loc[keep].reset_index(drop=True),
        entity_name=entity_name.loc[keep].reset_index(drop=True),
        periods=periods.loc[keep].reset_index(drop=True),
        rates={k: v.loc[keep].reset_index(drop=True) for k, v in rates.items()},
        field_id=_slice_opt(field_id, keep),
        field_name=_slice_opt(field_name, keep),
        facility_id=_slice_opt(facility_id, keep),
        facility_name=_slice_opt(facility_name, keep),
        basin=_slice_opt(basin, keep),
        region=_slice_opt(region, keep),
        environment=_slice_opt(environment, keep),
        operator=_slice_opt(operator, keep),
    )


def _assemble_arrow_table(
    *,
    source: str,
    entity_type: schema.EntityType,
    entity_id: pd.Series,
    entity_name: pd.Series,
    periods: pd.Series,
    rates: Mapping[str, pd.Series],
    field_id: pd.Series | None,
    field_name: pd.Series | None,
    facility_id: pd.Series | None,
    facility_name: pd.Series | None,
    basin: pd.Series | None,
    region: pd.Series | None,
    environment: pd.Series | None,
    operator: pd.Series | None,
) -> pa.Table:
    n = len(entity_id)
    period_dates = pa.array(periods.dt.date.tolist(), type=pa.date32())
    columns: dict[str, pa.Array] = {
        "source": pa.array([source] * n, type=pa.string()),
        "entity_type": pa.array([entity_type.value] * n, type=pa.string()),
        "entity_id": _clean_string_series(entity_id, n, fill=""),
        "entity_name": _clean_string_series(entity_name, n, fill=""),
        "field_id": _clean_string_series(field_id, n, fill=""),
        "field_name": _clean_string_series(field_name, n, fill=None),
        "facility_id": _clean_string_series(facility_id, n, fill=""),
        "facility_name": _clean_string_series(facility_name, n, fill=None),
        "basin": _clean_string_series(basin, n, fill=None),
        "region": _clean_string_series(region, n, fill=None),
        "environment": _clean_string_series(environment, n, fill=None),
        "operator": _clean_string_series(operator, n, fill=None),
        "period": period_dates,
    }
    for rate_col in schema.RATE_COLUMNS:
        columns[rate_col] = _clean_rate_series(rates.get(rate_col), n)
    table = pa.table(columns, schema=schema.PRODUCTION_SCHEMA)
    return schema.validate_production(table)


class Source(ABC):
    """Abstract base class for a public oil & gas data connector."""

    name: str
    country: str
    country_code: str
    publisher: str
    entity_type: schema.EntityType
    docs_url: str
    licence: str = "Public open data"
    licence_url: str = ""
    known_gaps: tuple[KnownGap, ...] = ()
    #: Include geo/wells in Layer-2 tiles (False for high-well-count onshore sources).
    web_wells: bool = True
    #: Include non-producing wellbores in Layer-2 search/index.json (default: producing only).
    search_all_wells: bool = False

    @abstractmethod
    def discover_production(
        self,
        *,
        client: httpx2.Client | None = None,
        year: int | None = None,
        environment: str | None = None,
        recent: bool = False,
    ) -> list[RemoteFile]:
        """List upstream production files matching the requested filters."""

    def fetch_production(
        self,
        remote: RemoteFile,
        dest: Path,
        *,
        client: httpx2.Client,
    ) -> DownloadResult:
        """Download one upstream production file into ``dest``."""
        return download_file(client, remote.url, dest)

    @abstractmethod
    def parse_production(self, raw: RawFile) -> pa.Table:
        """Pure, offline parser returning a validated canonical production Arrow table."""

    def fetch_geo(
        self,
        kind: schema.GeoKind,
        dest_dir: Path,
        *,
        client: httpx2.Client | None = None,
    ) -> list[DownloadResult]:
        """Download upstream spatial files for ``kind`` into ``dest_dir``."""
        raise NotImplementedError(
            f"{self.name!r} does not implement spatial download for {kind.value}; "
            f"see {self.docs_url}"
        )

    def parse_geo(self, kind: schema.GeoKind, paths: list[Path]) -> gpd.GeoDataFrame:
        """Pure, offline parser returning a GeoDataFrame for ``kind``."""
        raise NotImplementedError(
            f"{self.name!r} does not implement spatial parsing for {kind.value}; "
            f"see {self.docs_url}"
        )


_REGISTRY: dict[str, Source] = {}


def register[T: type[Source]](cls: T) -> T:
    """Register a :class:`Source` subclass under its ``name``."""
    instance = cls()
    _REGISTRY[instance.name] = instance
    return cls


def get(name: str) -> Source:
    """Look up a registered connector by name."""
    try:
        return _REGISTRY[name]
    except KeyError as err:
        choices = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"unknown source {name!r}; available: {choices}") from err


def available() -> list[str]:
    """Sorted names of all registered connectors."""
    return sorted(_REGISTRY)
