"""Tests for scripts/publish_mirror.py and scripts/ci_refresh.py (fully offline)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import pytest
import shapely

from ogprod import MirrorError, Store, mirror, schema
from ogprod.sources import base
from ogprod.store import write_geo, write_production
from scripts import ci_refresh, publish_mirror


@dataclass(frozen=True)
class FakeAdd:
    path_in_repo: str
    path_or_fileobj: Any


@dataclass(frozen=True)
class FakeDelete:
    path_in_repo: str


class FakeHfApi:
    """In-memory mock of ``huggingface_hub.HfApi`` for offline publish testing."""

    def __init__(
        self,
        *,
        sha: str = "abc123",
        manifest: dict[str, Any] | None = None,
        remote_files: list[str] | None = None,
        tmp_dir: Path,
    ) -> None:
        self.sha = sha
        self.manifest = manifest
        self.remote_files = list(remote_files or [])
        if manifest is not None and "manifest.json" not in self.remote_files:
            self.remote_files.append("manifest.json")
        self.tmp_dir = tmp_dir
        self.commits: list[dict[str, Any]] = []
        self.tags: list[str] = []

    def repo_info(self, *, repo_id: str, repo_type: str) -> Any:
        del repo_id, repo_type
        return type("Info", (), {"sha": self.sha})()

    def list_repo_files(
        self, *, repo_id: str, repo_type: str, revision: str | None = None
    ) -> list[str]:
        del repo_id, repo_type, revision
        return list(self.remote_files)

    def hf_hub_download(
        self, *, repo_id: str, filename: str, repo_type: str, revision: str | None = None
    ) -> str:
        del repo_id, repo_type, revision
        assert filename == "manifest.json"
        assert self.manifest is not None
        path = self.tmp_dir / "remote_manifest.json"
        path.write_text(json.dumps(self.manifest), encoding="utf-8")
        return str(path)

    def create_commit(self, **kwargs: Any) -> Any:
        self.commits.append(kwargs)
        return type(
            "CommitInfo", (), {"commit_url": "https://huggingface.co/datasets/x/commit/1"}
        )()

    def create_tag(self, *, tag: str, **kwargs: Any) -> None:
        del kwargs
        self.tags.append(tag)


def _populate_store(
    root: Path,
    *,
    source: str = "norway",
    rows: list[tuple[str, date, float]],
    geo_count: int = 2,
) -> None:
    table = base.build_production_table(
        source=source,
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series([r[0] for r in rows]),
        entity_name=pd.Series([r[0] for r in rows]),
        period=pd.Series([r[1] for r in rows]),
        field_id=pd.Series([r[0] for r in rows]),
        field_name=pd.Series([r[0] for r in rows]),
        rates={"oil_prod": pd.Series([r[2] for r in rows])},
    )
    write_production(table, root=root)
    gdf = gpd.GeoDataFrame(
        {
            "source": [source] * geo_count,
            "facility_id": [f"FAC-{i}" for i in range(geo_count)],
            "facility_name": [f"FAC-{i}" for i in range(geo_count)],
        },
        geometry=[shapely.Point(2.0 + i * 0.1, 60.0) for i in range(geo_count)],
        crs="EPSG:4326",
    )
    write_geo(gdf, schema.GeoKind.FACILITIES, root=root)


def test_publish_merges_without_dropping_untouched_and_skips_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gh_out = tmp_path / "gh_output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))

    data_dir = tmp_path / "data"
    _populate_store(
        data_dir,
        source="norway",
        rows=[("F1", date(2026, 1, 1), 1000.0), ("F2", date(2026, 1, 1), 2000.0)],
    )
    local_scanned = publish_mirror.scan_local_store(data_dir / "store")
    existing_remote = mirror.merge_mirror_manifest(
        None,
        {
            "brazil": {
                "country": "Brazil",
                "production": {
                    "2025": {
                        "path": "production/source=brazil/year=2025/data.parquet",
                        "sha256": "deadbeef",
                        "bytes": 100,
                        "rows": 10,
                        "sum_rates": {"oil_prod": 500.0},
                    }
                },
                "geo": {},
            }
        },
    )

    api = FakeHfApi(sha="head-sha-1", manifest=existing_remote, tmp_dir=tmp_path)
    res = publish_mirror.publish_store(
        data_dir=data_dir,
        repo_id="mralbu/ogprod",
        tag="v2-2026-01",
        api=api,
        operation_classes=(FakeAdd, FakeDelete),
    )
    assert res.changed is True
    assert res.uploaded == 2  # norway 2026 + facilities
    assert res.skipped == 0
    assert len(api.commits) == 1
    assert api.commits[0]["parent_commit"] == "head-sha-1"
    assert api.tags == ["v2-2026-01"]
    assert "changed=true" in gh_out.read_text(encoding="utf-8")

    # Verify manifest preserved untouched brazil 2025 partition.
    manifest_op = next(
        op for op in api.commits[0]["operations"] if op.path_in_repo == "manifest.json"
    )
    pushed_manifest = json.loads(manifest_op.path_or_fileobj.decode("utf-8"))
    assert "brazil" in pushed_manifest["sources"]
    assert "2025" in pushed_manifest["sources"]["brazil"]["production"]
    assert "norway" in pushed_manifest["sources"]
    assert pushed_manifest["sources"]["norway"]["production"]["2026"]["rows"] == 2
    assert local_scanned["norway"]["production"]["2026"]["rows"] == 2

    # Second run against the pushed manifest skips everything and does not commit.
    api2 = FakeHfApi(sha="head-sha-2", manifest=pushed_manifest, tmp_dir=tmp_path)
    res2 = publish_mirror.publish_store(
        data_dir=data_dir,
        repo_id="mralbu/ogprod",
        api=api2,
        operation_classes=(FakeAdd, FakeDelete),
    )
    assert res2.changed is False
    assert res2.skipped == 2
    assert len(api2.commits) == 0


def test_publish_sanity_gate_blocks_drops_and_force_overrides(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_store(
        data_dir,
        source="norway",
        rows=[("F1", date(2026, 1, 1), 100.0)],
        geo_count=1,
    )
    remote = mirror.merge_mirror_manifest(
        None,
        {
            "norway": {
                "production": {
                    "2026": {
                        "path": "production/source=norway/year=2026/data.parquet",
                        "sha256": "old",
                        "bytes": 500,
                        "rows": 100,
                        "sum_rates": {"oil_prod": 5000.0},
                    }
                },
                "geo": {
                    "facilities": {
                        "path": "geo/facilities/norway.parquet",
                        "sha256": "oldgeo",
                        "bytes": 500,
                        "rows": 10,
                    }
                },
            }
        },
    )
    api = FakeHfApi(manifest=remote, tmp_dir=tmp_path)
    with pytest.raises(publish_mirror.SanityGateError, match="row count dropped"):
        publish_mirror.publish_store(
            data_dir=data_dir,
            repo_id="mralbu/ogprod",
            api=api,
            operation_classes=(FakeAdd, FakeDelete),
        )
    assert len(api.commits) == 0

    # --force overrides sanity gate; --dry-run avoids commit.
    dry = publish_mirror.publish_store(
        data_dir=data_dir,
        repo_id="mralbu/ogprod",
        force=True,
        dry_run=True,
        api=api,
        operation_classes=(FakeAdd, FakeDelete),
    )
    assert dry.changed is True
    assert dry.dry_run is True
    assert len(api.commits) == 0


def test_publish_prune_legacy_cuts_over_v1_to_v2(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    _populate_store(
        data_dir,
        source="norway",
        rows=[("F1", date(2026, 1, 1), 1000.0)],
    )
    v1_manifest = {"schema_version": 1, "sources": {"anp": {}, "sodir": {}}}
    legacy_files = [
        ".gitattributes",
        "README.md",
        "manifest.json",
        "entities/anp.parquet",
        "production/source=anp/year=2025/data.parquet",
        "production/source=sodir/year=2025/data.parquet",
    ]
    api = FakeHfApi(manifest=v1_manifest, remote_files=legacy_files, tmp_dir=tmp_path)

    with pytest.raises(MirrorError, match="--prune-legacy"):
        publish_mirror.publish_store(
            data_dir=data_dir,
            repo_id="mralbu/ogprod",
            api=api,
            operation_classes=(FakeAdd, FakeDelete),
        )

    res = publish_mirror.publish_store(
        data_dir=data_dir,
        repo_id="mralbu/ogprod",
        prune_legacy=True,
        api=api,
        operation_classes=(FakeAdd, FakeDelete),
    )
    assert res.changed is True
    assert res.deleted == 3
    ops = api.commits[0]["operations"]
    deleted_paths = sorted(op.path_in_repo for op in ops if isinstance(op, FakeDelete))
    assert deleted_paths == [
        "entities/anp.parquet",
        "production/source=anp/year=2025/data.parquet",
        "production/source=sodir/year=2025/data.parquet",
    ]
    added_paths = {op.path_in_repo for op in ops if isinstance(op, FakeAdd)}
    assert "README.md" in added_paths
    assert "manifest.json" in added_paths


def test_ci_refresh_isolates_failing_source_and_publishes_healthy(tmp_path: Path) -> None:
    published_calls: list[dict[str, Any]] = []

    class StubStore(Store):
        def ingest(self, source: str, **kwargs: Any) -> Any:
            del kwargs
            if source == "mexico":
                raise ConnectionResetError("simulated portal reset")
            return type(
                "Summary",
                (),
                {"production_rows": 5, "partitions": (tmp_path / "p.parquet",), "geo_counts": {}},
            )()

    def fake_publish(**kwargs: Any) -> publish_mirror.PublishResult:
        published_calls.append(kwargs)
        return publish_mirror.PublishResult(
            repo_id="mralbu/ogprod", changed=True, uploaded=1, skipped=0, deleted=0
        )

    outcome = ci_refresh.run_refresh(
        ["norway", "mexico"],
        data_dir=tmp_path,
        db=StubStore(tmp_path),
        publish_fn=fake_publish,
    )
    assert outcome.succeeded == ("norway",)
    assert "mexico" in outcome.failed
    assert len(published_calls) == 1
    assert published_calls[0]["selected_sources"] == ["norway"]
    assert outcome.publish is not None
    assert outcome.publish.changed is True
