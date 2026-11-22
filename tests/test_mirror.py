"""Offline tests for canonical mirror layout, manifest v1, and Store mirror ingest.

All HTTP is served by ``httpx2.MockTransport``; nothing touches the network.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import httpx2
import pandas as pd
import pytest
from shapely.geometry import Polygon

import ogprod
from ogprod import mirror, schema, store


def _make_production(source: str, year: int, *, volume: float = 100.0) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "source": source,
                "entity_type": "field",
                "entity_id": "46437",
                "entity_name": "TROLL",
                "field": "TROLL",
                "basin": None,
                "region": "North sea",
                "operator": "Equinor",
                "period": pd.Timestamp(f"{year}-01-01"),
                "product": "oil",
                "volume": volume,
            },
            {
                "source": source,
                "entity_type": "field",
                "entity_id": "46437",
                "entity_name": "TROLL",
                "field": "TROLL",
                "basin": None,
                "region": "North sea",
                "operator": "Equinor",
                "period": pd.Timestamp(f"{year}-06-01"),
                "product": "gas",
                "volume": volume * 10.0,
            },
        ]
    )
    for col in schema._STRING_COLUMNS:
        frame[col] = frame[col].astype("string")
    return schema.validate(frame[list(schema.CANONICAL_COLUMNS)])


def _make_entities(source: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "source": [source],
            "entity_type": ["field"],
            "entity_id": ["46437"],
            "entity_name": ["TROLL"],
            "field": ["TROLL"],
            "basin": [None],
            "region": ["North sea"],
            "operator": ["Equinor"],
            "status": ["Producing"],
            "geometry": [Polygon([(3.5, 60.6), (3.6, 60.6), (3.6, 60.7), (3.5, 60.6)])],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def _build_mirror_fixture(
    staging: Path,
    *,
    source: str = "sodir",
    years: tuple[int, ...] = (2023, 2024),
    volumes: dict[int, float] | None = None,
    include_entities: bool = True,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build a valid manifest v1 + file payload map from real canonical Parquet files."""
    vols = volumes or {}
    payloads: dict[str, bytes] = {}
    prod_entries: dict[str, Any] = {}

    for year in years:
        (path,) = store.write_production(
            _make_production(source, year, volume=vols.get(year, 100.0)),
            root=staging,
        )
        entry = mirror.production_partition_entry(staging, path, updated_at="2026-11-22T12:00:00Z")
        prod_entries[str(year)] = entry
        payloads[entry["path"]] = path.read_bytes()

    source_block: dict[str, Any] = {"production": prod_entries}
    if include_entities:
        (ent_path,) = store.write_entities(_make_entities(source), root=staging)
        ent_entry = mirror.entities_entry(staging, ent_path, updated_at="2026-11-22T12:00:00Z")
        source_block["entities"] = ent_entry
        payloads[ent_entry["path"]] = ent_path.read_bytes()

    manifest = {
        "schema_version": 1,
        "updated_at": "2026-11-22T12:00:00Z",
        "sources": {source: source_block},
    }
    return manifest, payloads


def _mock_client(
    manifest: dict[str, Any],
    payloads: dict[str, bytes],
    *,
    requests_log: list[str] | None = None,
    repo_prefix: str = "/datasets/mralbu/ogprod/resolve/main/",
    fail_paths: set[str] | None = None,
) -> httpx2.Client:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests_log is not None:
            requests_log.append(str(request.url))
        path = request.url.path
        if not path.startswith(repo_prefix):
            return httpx2.Response(404)
        rel = path[len(repo_prefix) :]
        if fail_paths and rel in fail_paths:
            return httpx2.Response(500)
        if rel == "manifest.json":
            return httpx2.Response(200, json=manifest)
        if rel in payloads:
            return httpx2.Response(200, content=payloads[rel])
        return httpx2.Response(404)

    return httpx2.Client(transport=httpx2.MockTransport(handler), follow_redirects=True)


def test_resolve_mirror_precedence(monkeypatch):
    monkeypatch.delenv("OGPROD_MIRROR", raising=False)
    assert mirror.resolve_mirror() == "https://huggingface.co/datasets/mralbu/ogprod"

    monkeypatch.setenv("OGPROD_MIRROR", "acme/custom-ogprod")
    assert mirror.resolve_mirror() == "https://huggingface.co/datasets/acme/custom-ogprod"
    assert mirror.resolve_mirror("other/repo") == "https://huggingface.co/datasets/other/repo"
    assert mirror.resolve_mirror("https://mirror.example.org/data/") == (
        "https://mirror.example.org/data"
    )


def test_mirror_full_sync_and_checksum_skip(tmp_path):
    manifest, payloads = _build_mirror_fixture(tmp_path / "staging")
    seen: list[str] = []
    client = _mock_client(manifest, payloads, requests_log=seen)
    db = ogprod.Store(tmp_path / "local")

    result = db.ingest("sodir", client=client)
    assert result.source == "sodir"
    assert result.rows == 4
    assert result.first_period == pd.Timestamp("2023-01-01")
    assert result.last_period == pd.Timestamp("2024-06-01")
    assert len(result.partitions) == 2
    assert result.entities == 1
    assert len(db.production(source="sodir")) == 4
    assert len(db.entities(source="sodir")) == 1

    # Second run: only manifest.json is fetched; 0 partitions written, entities unchanged.
    seen.clear()
    again = db.ingest("sodir", client=client)
    assert len(seen) == 1
    assert seen[0].endswith("/resolve/main/manifest.json")
    assert again.partitions == ()
    assert again.entities is None
    assert again.rows == 4
    assert "0 partition(s); entities: unchanged" in str(again)

    # refresh=True forces re-download of partitions and entities even on matching SHA-256.
    seen.clear()
    refreshed = db.ingest("sodir", refresh=True, client=client)
    assert len(refreshed.partitions) == 2
    assert refreshed.entities == 1
    assert len(seen) == 4  # manifest + 2 partitions + entities


def test_mirror_selective_years_and_revision_routing(tmp_path):
    manifest, payloads = _build_mirror_fixture(tmp_path / "staging", years=(2022, 2023, 2024))
    seen: list[str] = []
    client = _mock_client(
        manifest,
        payloads,
        requests_log=seen,
        repo_prefix="/datasets/acme/ogprod/resolve/2026-11/",
    )
    db = ogprod.Store(tmp_path / "local")

    one = db.ingest("sodir", year=2023, revision="2026-11", mirror="acme/ogprod", client=client)
    assert one.rows == 2
    assert [p.parent.name for p in one.partitions] == ["year=2023"]
    assert all("/datasets/acme/ogprod/resolve/2026-11/" in url for url in seen)

    seen.clear()
    multi = db.ingest(
        "sodir",
        year=[2022, 2023],
        revision="2026-11",
        mirror="acme/ogprod",
        client=client,
    )
    # 2023 already matches checksum so only 2022 is downloaded.
    assert [p.parent.name for p in multi.partitions] == ["year=2022"]
    assert multi.rows == 4
    assert multi.entities is None


def test_mirror_updated_checksum_replaces_partition_atomically(tmp_path):
    manifest_v1, payloads_v1 = _build_mirror_fixture(
        tmp_path / "staging_v1", years=(2024,), volumes={2024: 100.0}
    )
    db = ogprod.Store(tmp_path / "local")
    db.ingest("sodir", client=_mock_client(manifest_v1, payloads_v1))
    assert db.production(product="oil")["volume"].iloc[0] == pytest.approx(100.0)

    manifest_v2, payloads_v2 = _build_mirror_fixture(
        tmp_path / "staging_v2", years=(2024,), volumes={2024: 250.0}
    )
    updated = db.ingest("sodir", client=_mock_client(manifest_v2, payloads_v2))
    assert len(updated.partitions) == 1
    assert db.production(product="oil")["volume"].iloc[0] == pytest.approx(250.0)


@pytest.mark.parametrize("corruption", ["sha256", "bytes"])
def test_mirror_corrupted_download_cleans_tmp_and_preserves_prior_file(tmp_path, corruption):
    manifest_v1, payloads_v1 = _build_mirror_fixture(
        tmp_path / "staging_v1", years=(2024,), volumes={2024: 100.0}
    )
    db = ogprod.Store(tmp_path / "local")
    db.ingest("sodir", client=_mock_client(manifest_v1, payloads_v1))
    target = tmp_path / "local/production/source=sodir/year=2024/data.parquet"
    original_bytes = target.read_bytes()

    manifest_v2, payloads_v2 = _build_mirror_fixture(
        tmp_path / "staging_v2", years=(2024,), volumes={2024: 250.0}
    )
    rel = "production/source=sodir/year=2024/data.parquet"
    if corruption == "sha256":
        # Same length, flipped bytes -> triggers SHA-256 mismatch.
        raw = bytearray(payloads_v2[rel])
        raw[0] ^= 0xFF
        payloads_v2[rel] = bytes(raw)
    else:
        # Truncated payload -> triggers byte-length mismatch.
        payloads_v2[rel] = payloads_v2[rel][:-10]

    with pytest.raises(RuntimeError, match="checksum|corrupted"):
        db.ingest("sodir", client=_mock_client(manifest_v2, payloads_v2))

    assert target.read_bytes() == original_bytes
    assert not target.with_name("data.parquet.tmp").exists()


def test_mirror_rejects_unsupported_schema_version(tmp_path):
    manifest = {"schema_version": 99, "updated_at": "2026-11-22T12:00:00Z", "sources": {}}
    client = _mock_client(manifest, {})
    with pytest.raises(RuntimeError, match="upgrade ogprod"):
        ogprod.Store(tmp_path).ingest("sodir", client=client)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../escape.parquet",
        "production/../../escape.parquet",
        "/etc/passwd",
        "production\\source=sodir\\data.parquet",
    ],
)
def test_mirror_rejects_path_traversal(tmp_path, bad_path):
    manifest, payloads = _build_mirror_fixture(tmp_path / "staging", years=(2024,))
    hostile = copy.deepcopy(manifest)
    hostile["sources"]["sodir"]["production"]["2024"]["path"] = bad_path
    client = _mock_client(hostile, payloads)
    with pytest.raises(ValueError, match="unsafe manifest path"):
        ogprod.Store(tmp_path / "local").ingest("sodir", client=client)
    assert not (tmp_path / "escape.parquet").exists()


def test_mirror_option_and_origin_validation(tmp_path, data_dir):
    db = ogprod.Store(tmp_path)
    sample = data_dir / "sodir_field_production_monthly_sample.csv"

    with pytest.raises(ValueError, match="origin must be"):
        bad_origin: Any = "ftp"
        db.ingest("sodir", origin=bad_origin)

    with pytest.raises(TypeError, match="origin='publisher'"):
        db.ingest("anp", year=2023, month=1)

    with pytest.raises(TypeError, match="unsupported mirror ingest option"):
        db.ingest("anp", bogus=1)

    with pytest.raises(TypeError, match="year must be"):
        db.ingest("anp", year="2023")

    with pytest.raises(TypeError, match="revision="):
        db.ingest("sodir", files=[sample], revision="2026-11")

    with pytest.raises(TypeError, match="mirror="):
        db.ingest("sodir", files=[sample], mirror="acme/ogprod")

    with pytest.raises(TypeError, match="revision="):
        db.ingest("sodir", origin="publisher", revision="2026-11")

    with pytest.raises(TypeError, match="mirror="):
        db.ingest("sodir", origin="publisher", mirror="acme/ogprod")


def test_mirror_missing_year_and_missing_source_errors(tmp_path):
    manifest, payloads = _build_mirror_fixture(tmp_path / "staging", years=(2022, 2023))
    client = _mock_client(manifest, payloads)
    db = ogprod.Store(tmp_path / "local")

    with pytest.raises(ValueError, match=r"2025.*2022\.\.2023.*origin='publisher'"):
        db.ingest("sodir", year=2025, client=client)

    with pytest.raises(ValueError, match=r"not available on mirror.*origin='publisher'"):
        db.ingest("anp", client=client)


def test_mirror_source_without_entities_sets_entities_none(tmp_path):
    manifest, payloads = _build_mirror_fixture(
        tmp_path / "staging", years=(2024,), include_entities=False
    )
    client = _mock_client(manifest, payloads)
    result = ogprod.Store(tmp_path / "local").ingest("sodir", client=client)
    assert result.rows == 2
    assert result.entities is None


def test_mirror_mid_run_failure_notes_written_partitions_and_resumes(tmp_path):
    manifest, payloads = _build_mirror_fixture(tmp_path / "staging", years=(2023, 2024))
    failing_rel = "production/source=sodir/year=2024/data.parquet"
    failing_client = _mock_client(manifest, payloads, fail_paths={failing_rel})
    db = ogprod.Store(tmp_path / "local")

    with pytest.raises(httpx2.HTTPStatusError) as caught:
        db.ingest("sodir", client=failing_client)

    (note,) = caught.value.__notes__
    assert "source=sodir/year=2023/data.parquet" in note
    assert (tmp_path / "local/production/source=sodir/year=2023/data.parquet").exists()
    assert not (tmp_path / "local/production/source=sodir/year=2024/data.parquet").exists()

    # Re-running skips 2023 and downloads only 2024 + entities.
    seen: list[str] = []
    resumed = db.ingest("sodir", client=_mock_client(manifest, payloads, requests_log=seen))
    assert [p.parent.name for p in resumed.partitions] == ["year=2024"]
    assert resumed.entities == 1
    assert not any("year=2023" in url for url in seen)
    assert len(db.production(source="sodir")) == 4


def test_manifest_json_round_trip_matches_v1_schema(tmp_path):
    manifest, _ = _build_mirror_fixture(tmp_path / "staging", years=(2023,))
    encoded = json.loads(json.dumps(manifest))
    validated = mirror.validate_manifest(encoded)
    prod = validated["sources"]["sodir"]["production"]["2023"]
    assert set(prod) == {
        "path",
        "sha256",
        "bytes",
        "rows",
        "first_period",
        "last_period",
        "totals_m3",
        "updated_at",
    }
    assert prod["totals_m3"] == pytest.approx({"gas": 1000.0, "oil": 100.0})
    ent = validated["sources"]["sodir"]["entities"]
    assert set(ent) == {"path", "sha256", "bytes", "rows", "updated_at"}
