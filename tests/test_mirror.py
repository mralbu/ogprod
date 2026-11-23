"""Tests for ogprod.mirror (anonymous HTTPS sync, checksums, safety, manifest merge)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx2
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st

from ogprod import DEFAULT_MIRROR, MirrorError, Store, mirror, schema
from ogprod.sources import base
from ogprod.store import write_geo, write_production


def _seed_source_store(
    root: Path, source: str = "brazil"
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build a tiny valid v2 Layer-1 store and return ``(mirror_manifest, url_path -> bytes)``."""
    table = base.build_production_table(
        source=source,
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["W1", "W1"]),
        entity_name=pd.Series(["W1", "W1"]),
        period=pd.Series([date(2025, 1, 1), date(2026, 1, 1)]),
        field_id=pd.Series(["TUPI", "TUPI"]),
        field_name=pd.Series(["TUPI", "TUPI"]),
        facility_id=pd.Series(["FPSO A", "FPSO A"]),
        facility_name=pd.Series(["FPSO A", "FPSO A"]),
        basin=pd.Series(["Santos", "Santos"]),
        region=pd.Series(["Rio de Janeiro", "Rio de Janeiro"]),
        environment=pd.Series(["offshore", "offshore"]),
        rates={
            "oil_prod": pd.Series([1000.0, 1200.0]),
            "gas_prod": pd.Series([50.0, 60.0]),
        },
    )
    write_production(table, root=root)
    fac = gpd.GeoDataFrame(
        {
            "source": [source],
            "facility_id": ["FPSO A"],
            "facility_name": ["FPSO A"],
            "oil_capacity_bpd": [180000.0],
        },
        geometry=[shapely.Point(-42.5, -24.5)],
        crs="EPSG:4326",
    )
    write_geo(fac, schema.GeoKind.FACILITIES, root=root)

    store_root = root / "store"
    files: dict[str, bytes] = {}
    prod_entries: dict[str, Any] = {}
    for year in (2025, 2026):
        rel = f"production/source={source}/year={year}/data.parquet"
        path = store_root / rel
        files[rel] = path.read_bytes()
        prod_entries[str(year)] = mirror.compute_partition_stats(path, rel)

    fac_rel = f"geo/facilities/{source}.parquet"
    fac_path = store_root / fac_rel
    files[fac_rel] = fac_path.read_bytes()
    geo_entries = {"facilities": mirror.compute_geo_stats(fac_path, fac_rel)}

    manifest = mirror.merge_mirror_manifest(
        None,
        {
            source: {
                "country": "Brazil",
                "country_code": "BR",
                "publisher": "ANP",
                "docs_url": "https://example.com",
                "entity_type": "well",
                "production": prod_entries,
                "geo": geo_entries,
            }
        },
    )
    return manifest, files


def _mock_mirror_client(
    manifest: dict[str, Any],
    files: dict[str, bytes],
    *,
    counter: list[str] | None = None,
    override_bytes: dict[str, bytes] | None = None,
) -> httpx2.Client:
    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if counter is not None:
            counter.append(url)
        if url.endswith("/manifest.json"):
            return httpx2.Response(200, json=manifest)
        for rel, payload in files.items():
            if url.endswith(f"/{rel}"):
                data = (override_bytes or {}).get(rel, payload)
                return httpx2.Response(200, content=data)
        return httpx2.Response(404)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


@given(
    years=st.lists(st.integers(min_value=1900, max_value=2100), min_size=1, max_size=20),
)
def test_normalize_years_sorted_unique_and_idempotent(years: list[int]) -> None:
    norm = mirror.normalize_years(years)
    assert norm is not None
    assert norm == sorted(set(years))
    assert mirror.normalize_years(norm) == norm


@given(
    bad_path=st.one_of(
        st.just(""),
        st.just("/etc/passwd"),
        st.just("production/../secret.parquet"),
        st.just("geo/fields/../../escape.parquet"),
        st.just("geo\\fields\\brazil.parquet"),
        st.just("other/source=brazil/data.parquet"),
        st.text(min_size=1, max_size=20).map(lambda s: f"../{s}"),
    )
)
def test_validate_rel_path_rejects_unsafe_paths(bad_path: str) -> None:
    with pytest.raises(MirrorError):
        mirror.validate_rel_path(bad_path)


def test_mirror_resolution_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OGPROD_MIRROR", raising=False)
    assert mirror.resolve_mirror() == DEFAULT_MIRROR
    monkeypatch.setenv("OGPROD_MIRROR", "custom/repo")
    assert mirror.resolve_mirror() == "custom/repo"
    assert mirror.resolve_mirror("explicit/repo") == "explicit/repo"
    assert mirror.mirror_base_url("explicit/repo", revision="v2.0") == (
        "https://huggingface.co/datasets/explicit/repo/resolve/v2.0"
    )
    assert mirror.mirror_base_url("https://cdn.example.com/ogprod/") == (
        "https://cdn.example.com/ogprod"
    )


def test_store_ingest_defaults_to_mirror_and_skips_unchanged(tmp_path: Path) -> None:
    seed_root = tmp_path / "seed"
    manifest, files = _seed_source_store(seed_root, "brazil")

    requests: list[str] = []
    client = _mock_mirror_client(manifest, files, counter=requests)
    db = Store(tmp_path / "dest")

    summary = db.ingest("brazil", client=client)
    assert summary.files_ingested == 3  # 2025, 2026, facilities
    assert summary.files_skipped == 0
    assert summary.production_rows == 2
    assert summary.geo_counts == {"facilities": 1}
    assert len(db.production(source="brazil")) == 2
    assert len(db.facilities(source="brazil")) == 1

    # Local manifest records mirror provenance.
    local_manifest = json.loads((db.root / "store" / "manifest.json").read_text(encoding="utf-8"))
    assert local_manifest["sources"]["brazil"]["mirror"] == DEFAULT_MIRROR
    assert local_manifest["sources"]["brazil"]["revision"] == "main"

    # Second run skips all unchanged files by checksum (only fetches manifest.json).
    requests.clear()
    summary2 = db.ingest("brazil", client=client)
    assert summary2.files_ingested == 0
    assert summary2.files_skipped == 3
    assert len(requests) == 1
    assert requests[0].endswith("/manifest.json")

    # refresh=True forces re-download.
    summary3 = db.ingest("brazil", refresh=True, client=client)
    assert summary3.files_ingested == 3
    assert summary3.files_skipped == 0


def test_mirror_sync_year_filtering_and_no_geo(tmp_path: Path) -> None:
    manifest, files = _seed_source_store(tmp_path / "seed", "brazil")
    client = _mock_mirror_client(manifest, files)
    db = Store(tmp_path / "dest")

    summary = db.ingest("brazil", year=[2026], geo_layers=False, client=client)
    assert summary.files_ingested == 1
    assert summary.production_rows == 1
    assert summary.geo_counts == {}
    assert len(db.production(source="brazil")) == 1
    assert len(db.facilities(source="brazil")) == 0

    with pytest.raises(MirrorError, match="1999"):
        db.ingest("brazil", year=1999, client=client)


def test_mirror_rejects_checksum_mismatch_and_cleans_tmp(tmp_path: Path) -> None:
    manifest, files = _seed_source_store(tmp_path / "seed", "brazil")
    bad_rel = "production/source=brazil/year=2026/data.parquet"
    corrupted = b"X" * len(files[bad_rel])
    client = _mock_mirror_client(manifest, files, override_bytes={bad_rel: corrupted})

    db = Store(tmp_path / "dest")
    with pytest.raises(MirrorError, match="sha256 mismatch"):
        db.ingest("brazil", year=2026, geo_layers=False, client=client)

    dest = db.root / "store" / bad_rel
    assert not dest.exists()
    assert not dest.with_name(f"{dest.name}.tmp").exists()


def test_mirror_rejects_unsupported_schema_version_and_publisher_options(tmp_path: Path) -> None:
    v1_manifest = {"schema_version": 1, "sources": {}}
    client = _mock_mirror_client(v1_manifest, {})
    db = Store(tmp_path / "dest")

    with pytest.raises(MirrorError, match="schema_version=1"):
        db.ingest("brazil", client=client)

    with pytest.raises(ValueError, match="origin='publisher'"):
        db.ingest("brazil", keep_raw=True, client=client)

    with pytest.raises(ValueError, match="origin='publisher'"):
        db.ingest("brazil", recent=True, client=client)


def test_parquet_writer_uses_128k_row_group_size(tmp_path: Path) -> None:
    n = 130_000
    table = base.build_production_table(
        source="brazil",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series([f"W{i}" for i in range(n)]),
        entity_name=pd.Series([f"W{i}" for i in range(n)]),
        period=pd.Series([date(2026, 1, 1)] * n),
        rates={"oil_prod": pd.Series([10.0] * n)},
    )
    parts = write_production(table, root=tmp_path)
    meta = pq.read_metadata(parts[0])
    assert meta.num_row_groups == 2
    assert meta.row_group(0).num_rows == 128_000
    assert meta.row_group(1).num_rows == 2_000
    assert pa.table({"a": [1]}).num_rows == 1
