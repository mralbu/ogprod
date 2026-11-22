"""Offline tests for scripts/publish_mirror.py and scripts/ci_refresh.py.

All Hugging Face API calls are mocked; nothing touches the network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
from huggingface_hub.errors import EntryNotFoundError
from scripts import ci_refresh, publish_mirror
from shapely.geometry import Point

import ogprod
from ogprod import schema, store


@dataclass
class _FakeRepoInfo:
    sha: str


@dataclass
class _FakeCommitInfo:
    oid: str


class FakeHfApi:
    """In-memory mock of ``huggingface_hub.HfApi`` for offline publish tests."""

    def __init__(
        self,
        tmp_path: Path,
        *,
        remote_manifest: dict[str, Any] | None = None,
        remote_oid: str = "oid-initial-111",
    ) -> None:
        self.tmp_path = tmp_path
        self.remote_manifest = remote_manifest
        self.remote_oid = remote_oid
        self.commits: list[dict[str, Any]] = []
        self.tags: list[dict[str, Any]] = []

    def repo_info(self, *, repo_id: str, repo_type: str, revision: str) -> _FakeRepoInfo:
        assert repo_type == "dataset"
        assert revision == "main"
        return _FakeRepoInfo(sha=self.remote_oid)

    def hf_hub_download(self, *, repo_id: str, filename: str, repo_type: str, revision: str) -> str:
        assert filename == "manifest.json"
        if self.remote_manifest is None:
            raise EntryNotFoundError("manifest.json not found")
        cache_file = self.tmp_path / "remote_manifest.json"
        cache_file.write_text(json.dumps(self.remote_manifest), encoding="utf-8")
        return str(cache_file)

    def create_commit(
        self,
        *,
        repo_id: str,
        operations: list[Any],
        commit_message: str,
        repo_type: str,
        parent_commit: str | None = None,
    ) -> _FakeCommitInfo:
        new_oid = f"oid-commit-{len(self.commits) + 1}"
        self.commits.append(
            {
                "repo_id": repo_id,
                "operations": operations,
                "commit_message": commit_message,
                "repo_type": repo_type,
                "parent_commit": parent_commit,
                "oid": new_oid,
            }
        )
        # Update stored remote manifest so subsequent calls see the committed state.
        for op in operations:
            if op.path_in_repo == "manifest.json":
                payload = op.path_or_fileobj
                raw = (
                    payload.decode("utf-8")
                    if isinstance(payload, bytes)
                    else Path(payload).read_text()
                )
                self.remote_manifest = json.loads(raw)
        self.remote_oid = new_oid
        return _FakeCommitInfo(oid=new_oid)

    def create_tag(self, *, repo_id: str, tag: str, revision: str, repo_type: str) -> None:
        self.tags.append(
            {"repo_id": repo_id, "tag": tag, "revision": revision, "repo_type": repo_type}
        )


def _write_prod(
    root: Path,
    source: str,
    year: int,
    *,
    n_wells: int = 10,
    oil_per_well: float = 100.0,
    gas_per_well: float = 500.0,
) -> Path:
    rows: list[dict[str, Any]] = []
    for idx in range(n_wells):
        base_row = {
            "source": source,
            "entity_type": "well",
            "entity_id": f"W-{idx:03d}",
            "entity_name": f"W-{idx:03d}",
            "field": "F",
            "basin": "B",
            "region": "R",
            "operator": "OP",
            "period": pd.Timestamp(f"{year}-01-01"),
        }
        rows.append({**base_row, "product": "oil", "volume": oil_per_well})
        rows.append({**base_row, "product": "gas", "volume": gas_per_well})
    frame = pd.DataFrame(rows)
    for col in schema._STRING_COLUMNS:
        frame[col] = frame[col].astype("string")
    (path,) = store.write_production(frame[list(schema.CANONICAL_COLUMNS)], root=root)
    return path


def _write_ents(root: Path, source: str, *, n_wells: int = 10) -> Path:
    frame = gpd.GeoDataFrame(
        {
            "source": [source] * n_wells,
            "entity_type": ["well"] * n_wells,
            "entity_id": [f"W-{i:03d}" for i in range(n_wells)],
            "entity_name": [f"W-{i:03d}" for i in range(n_wells)],
            "field": ["F"] * n_wells,
            "basin": ["B"] * n_wells,
            "region": ["R"] * n_wells,
            "operator": ["OP"] * n_wells,
            "status": ["Producing"] * n_wells,
            "geometry": [Point(-40.0 - i * 0.01, -22.0) for i in range(n_wells)],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    (path,) = store.write_entities(frame, root=root)
    return path


def test_initial_seed_commit_tag_and_github_output(tmp_path, monkeypatch):
    local = tmp_path / "store"
    _write_prod(local, "sodir", 2024)
    _write_ents(local, "sodir")
    gh_out = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))

    api = FakeHfApi(tmp_path, remote_manifest=None, remote_oid="oid-seed-0")
    result = publish_mirror.publish_mirror(
        store_root=local,
        sources=["sodir"],
        repo="mralbu/ogprod",
        tag="2026-11",
        api=api,
        updated_at="2026-11-22T12:00:00Z",
    )

    assert result.changed is True
    assert result.commit_oid == "oid-commit-1"
    assert result.tag == "2026-11"
    assert len(api.commits) == 1
    assert api.commits[0]["parent_commit"] == "oid-seed-0"
    paths_uploaded = sorted(op.path_in_repo for op in api.commits[0]["operations"])
    assert paths_uploaded == [
        "entities/sodir.parquet",
        "manifest.json",
        "production/source=sodir/year=2024/data.parquet",
    ]
    assert api.tags == [
        {
            "repo_id": "mralbu/ogprod",
            "tag": "2026-11",
            "revision": "oid-commit-1",
            "repo_type": "dataset",
        }
    ]
    assert gh_out.read_text(encoding="utf-8") == "changed=true\n"


def test_preserves_untouched_sources_and_years_verbatim(tmp_path):
    local = tmp_path / "store"
    _write_prod(local, "argentina", 2026)

    untouched_anp = {
        "production": {
            "2023": {
                "path": "production/source=anp/year=2023/data.parquet",
                "sha256": "aaa",
                "bytes": 123,
                "rows": 500,
                "first_period": "2023-01-01",
                "last_period": "2023-12-01",
                "totals_m3": {"oil": 999.0},
                "updated_at": "2026-10-01T00:00:00Z",
            }
        },
        "entities": {
            "path": "entities/anp.parquet",
            "sha256": "bbb",
            "bytes": 45,
            "rows": 50,
            "updated_at": "2026-10-01T00:00:00Z",
        },
    }
    untouched_arg_2024 = {
        "path": "production/source=argentina/year=2024/data.parquet",
        "sha256": "ccc",
        "bytes": 456,
        "rows": 800,
        "first_period": "2024-01-01",
        "last_period": "2024-12-01",
        "totals_m3": {"oil": 1234.0},
        "updated_at": "2026-10-01T00:00:00Z",
    }
    prior = {
        "schema_version": 1,
        "updated_at": "2026-10-01T00:00:00Z",
        "sources": {
            "anp": untouched_anp,
            "argentina": {"production": {"2024": untouched_arg_2024}},
        },
    }

    api = FakeHfApi(tmp_path, remote_manifest=prior)
    result = publish_mirror.publish_mirror(
        store_root=local, sources=["argentina"], years=[2026], repo="mralbu/ogprod", api=api
    )

    assert result.changed is True
    assert result.merged_manifest["sources"]["anp"] == untouched_anp
    assert (
        result.merged_manifest["sources"]["argentina"]["production"]["2024"] == untouched_arg_2024
    )
    assert "2026" in result.merged_manifest["sources"]["argentina"]["production"]


def test_unchanged_partitions_produce_noop(tmp_path, monkeypatch):
    local = tmp_path / "store"
    _write_prod(local, "sodir", 2024)
    _write_ents(local, "sodir")
    gh_out = tmp_path / "github_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))

    api = FakeHfApi(tmp_path, remote_manifest=None)
    first = publish_mirror.publish_mirror(
        store_root=local, sources=["sodir"], repo="mralbu/ogprod", api=api
    )
    assert first.changed is True
    assert len(api.commits) == 1

    gh_out.write_text("", encoding="utf-8")
    second = publish_mirror.publish_mirror(
        store_root=local, sources=["sodir"], repo="mralbu/ogprod", api=api
    )
    assert second.changed is False
    assert second.skipped == 2
    assert len(api.commits) == 1  # no second commit
    assert gh_out.read_text(encoding="utf-8") == "changed=false\n"


def test_sanity_gate_rejects_row_drop_and_force_overrides(tmp_path):
    local = tmp_path / "store"
    _write_prod(local, "sodir", 2024, n_wells=10, oil_per_well=100.0)
    api = FakeHfApi(tmp_path, remote_manifest=None)
    publish_mirror.publish_mirror(
        store_root=local, sources=["sodir"], repo="mralbu/ogprod", api=api
    )

    # Rewrite with 8 wells (20% drop -> exceeds 15% threshold).
    local2 = tmp_path / "store2"
    _write_prod(local2, "sodir", 2024, n_wells=8, oil_per_well=125.0, gas_per_well=625.0)

    with pytest.raises(publish_mirror.SanityGateError, match="row count dropped"):
        publish_mirror.publish_mirror(
            store_root=local2, sources=["sodir"], repo="mralbu/ogprod", api=api
        )
    assert len(api.commits) == 1

    forced = publish_mirror.publish_mirror(
        store_root=local2, sources=["sodir"], repo="mralbu/ogprod", force=True, api=api
    )
    assert forced.changed is True
    assert len(api.commits) == 2


def test_sanity_gate_rejects_product_total_drop_and_force_overrides(tmp_path):
    local = tmp_path / "store"
    _write_prod(local, "sodir", 2024, n_wells=10, oil_per_well=100.0, gas_per_well=500.0)
    api = FakeHfApi(tmp_path, remote_manifest=None)
    publish_mirror.publish_mirror(
        store_root=local, sources=["sodir"], repo="mralbu/ogprod", api=api
    )

    # Same row count (10 wells), but oil drops 30% (100 -> 70).
    local2 = tmp_path / "store2"
    _write_prod(local2, "sodir", 2024, n_wells=10, oil_per_well=70.0, gas_per_well=500.0)

    with pytest.raises(publish_mirror.SanityGateError, match="oil total dropped"):
        publish_mirror.publish_mirror(
            store_root=local2, sources=["sodir"], repo="mralbu/ogprod", api=api
        )

    forced = publish_mirror.publish_mirror(
        store_root=local2, sources=["sodir"], repo="mralbu/ogprod", force=True, api=api
    )
    assert forced.changed is True


def test_sanity_gate_rejects_entity_drop_and_empty_files(tmp_path):
    local = tmp_path / "store"
    _write_ents(local, "sodir", n_wells=20)
    api = FakeHfApi(tmp_path, remote_manifest=None)
    publish_mirror.publish_mirror(
        store_root=local, sources=["sodir"], repo="mralbu/ogprod", api=api
    )

    # 17 entities -> 15% drop > 10% threshold.
    local2 = tmp_path / "store2"
    _write_ents(local2, "sodir", n_wells=17)
    with pytest.raises(publish_mirror.SanityGateError, match="entity count dropped"):
        publish_mirror.publish_mirror(
            store_root=local2, sources=["sodir"], repo="mralbu/ogprod", api=api
        )

    # Empty entity file -> rejected even on fresh repo.
    empty_root = tmp_path / "empty_store"
    empty_path = empty_root / "entities" / "sodir.parquet"
    empty_path.parent.mkdir(parents=True, exist_ok=True)
    schema.empty_entities().to_parquet(empty_path, index=False, compression="zstd")
    with pytest.raises(publish_mirror.SanityGateError, match="empty entities file"):
        publish_mirror.publish_mirror(
            store_root=empty_root,
            sources=["sodir"],
            repo="mralbu/ogprod",
            api=FakeHfApi(tmp_path, remote_manifest=None),
        )

    # Empty production partition -> rejected.
    empty_prod_root = tmp_path / "empty_prod"
    empty_prod = empty_prod_root / "production/source=sodir/year=2024/data.parquet"
    empty_prod.parent.mkdir(parents=True, exist_ok=True)
    schema.empty_frame().to_parquet(empty_prod, index=False, compression="zstd")
    with pytest.raises(publish_mirror.SanityGateError, match="empty production partition"):
        publish_mirror.publish_mirror(
            store_root=empty_prod_root,
            sources=["sodir"],
            repo="mralbu/ogprod",
            api=FakeHfApi(tmp_path, remote_manifest=None),
        )


def test_dry_run_and_missing_token_handling(tmp_path, monkeypatch):
    local = tmp_path / "store"
    _write_prod(local, "sodir", 2024)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="HF_TOKEN"):
        publish_mirror.publish_mirror(store_root=local, sources=["sodir"], repo="mralbu/ogprod")

    api = FakeHfApi(tmp_path, remote_manifest=None)
    result = publish_mirror.publish_mirror(
        store_root=local, sources=["sodir"], repo="mralbu/ogprod", dry_run=True, api=api
    )
    assert result.dry_run is True
    assert result.changed is False
    assert api.commits == []


def test_ci_refresh_policy_and_fault_isolation(tmp_path, monkeypatch):
    assert ci_refresh.scheduled_sources() == ["sodir", "argentina"]
    assert ci_refresh.years_for_source("sodir", current_year=2026) is None
    assert ci_refresh.years_for_source("argentina", current_year=2026) == [2026, 2025]
    assert ci_refresh.years_for_source("anp", current_year=2026) is None
    assert ci_refresh.years_for_source("argentina", current_year=2026, override_years=[2024]) == [
        2024
    ]

    # Case 1: argentina ingest fails -> sodir still publishes, overall exit code is 1.
    def fake_ingest(
        self, source, *, origin="mirror", year=None, entities=True, refresh=False, **kw
    ):
        assert origin == "publisher"
        if source == "argentina":
            raise RuntimeError("CKAN portal 503")
        _write_prod(self.root, source, 2026, n_wells=10)
        _write_ents(self.root, source, n_wells=10)
        return "ok"

    monkeypatch.setattr(ogprod.Store, "ingest", fake_ingest)
    api = FakeHfApi(tmp_path, remote_manifest=None)
    code = ci_refresh.run_refresh(
        sources=["sodir", "argentina"],
        repo="mralbu/ogprod",
        current_year=2026,
        api=api,
    )
    assert code == 1
    assert len(api.commits) == 1
    assert api.remote_manifest is not None
    assert "sodir" in api.remote_manifest["sources"]
    assert "argentina" not in api.remote_manifest["sources"]

    # Case 2: sodir trips sanity gate, argentina is healthy -> argentina still publishes, exit 1.
    def fake_ingest_gate(
        self, source, *, origin="mirror", year=None, entities=True, refresh=False, **kw
    ):
        if source == "sodir":
            # 50% drop vs committed 10 wells -> trips sanity gate.
            _write_prod(self.root, "sodir", 2026, n_wells=5)
            _write_ents(self.root, "sodir", n_wells=10)
        else:
            _write_prod(self.root, "argentina", year or 2026, n_wells=10)
            if entities:
                _write_ents(self.root, "argentina", n_wells=10)
        return "ok"

    monkeypatch.setattr(ogprod.Store, "ingest", fake_ingest_gate)
    code2 = ci_refresh.run_refresh(
        sources=["sodir", "argentina"],
        repo="mralbu/ogprod",
        current_year=2026,
        api=api,
    )
    assert code2 == 1
    assert len(api.commits) == 2
    assert api.remote_manifest is not None
    assert "argentina" in api.remote_manifest["sources"]
    assert set(api.remote_manifest["sources"]["argentina"]["production"]) == {"2025", "2026"}
    # sodir's prior good 10-well (20-row) partition was preserved.
    assert api.remote_manifest["sources"]["sodir"]["production"]["2026"]["rows"] == 20
