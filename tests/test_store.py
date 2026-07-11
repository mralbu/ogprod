import pandas as pd
import pytest

from ogprod import schema, store


def production_frame(rows: list[dict]) -> pd.DataFrame:
    """Build a canonical frame from row dicts (defaults filled in)."""
    defaults = {
        "source": "testsrc",
        "entity_type": "well",
        "entity_id": "W-1",
        "entity_name": "W-1",
        "field": "F",
        "basin": "B",
        "region": "R",
        "operator": "OP",
        "period": pd.Timestamp("2023-01-01"),
        "product": "oil",
        "volume": 100.0,
    }
    frame = pd.DataFrame([{**defaults, **row} for row in rows])
    for column in schema._STRING_COLUMNS:
        frame[column] = frame[column].astype("string")
    return schema.validate(frame[list(schema.CANONICAL_COLUMNS)])


@pytest.fixture
def root(tmp_path):
    return tmp_path / "store"


def test_write_creates_source_year_partitions(root):
    frame = production_frame(
        [
            {"period": pd.Timestamp("2023-01-01")},
            {"period": pd.Timestamp("2024-01-01")},
        ]
    )
    written = store.write_production(frame, root=root)
    assert sorted(p.relative_to(root).as_posix() for p in written) == [
        "production/source=testsrc/year=2023/data.parquet",
        "production/source=testsrc/year=2024/data.parquet",
    ]
    stored = pd.read_parquet(written[0])
    assert list(stored.columns) == list(schema.CANONICAL_COLUMNS)
    assert len(stored) == 1


def test_restatement_upserts_not_appends(root):
    store.write_production(production_frame([{"volume": 100.0}]), root=root)
    store.write_production(production_frame([{"volume": 250.0}]), root=root)  # revised
    stored = pd.read_parquet(root / "production/source=testsrc/year=2023/data.parquet")
    assert len(stored) == 1  # no duplicate (source, entity_id, period, product)
    assert stored["volume"].iloc[0] == 250.0


def test_upsert_preserves_untouched_rows_in_partition(root):
    store.write_production(
        production_frame(
            [
                {"entity_id": "W-1", "volume": 1.0},
                {"entity_id": "W-2", "volume": 2.0},
            ]
        ),
        root=root,
    )
    store.write_production(production_frame([{"entity_id": "W-1", "volume": 9.0}]), root=root)
    stored = pd.read_parquet(root / "production/source=testsrc/year=2023/data.parquet")
    assert len(stored) == 2
    assert stored.set_index("entity_id")["volume"].to_dict() == {"W-1": 9.0, "W-2": 2.0}


def test_write_leaves_other_partitions_untouched(root):
    store.write_production(production_frame([{"period": pd.Timestamp("2022-06-01")}]), root=root)
    other = root / "production/source=testsrc/year=2022/data.parquet"
    before = other.read_bytes()
    store.write_production(production_frame([{"period": pd.Timestamp("2023-06-01")}]), root=root)
    assert other.read_bytes() == before


def test_duplicate_keys_within_one_write_keep_last(root):
    frame = production_frame([{"volume": 1.0}, {"volume": 7.0}])  # same key twice
    store.write_production(frame, root=root)
    stored = pd.read_parquet(root / "production/source=testsrc/year=2023/data.parquet")
    assert len(stored) == 1
    assert stored["volume"].iloc[0] == 7.0


def test_write_rejects_invalid_frame(root):
    with pytest.raises(schema.SchemaError):
        store.write_production(pd.DataFrame({"nope": [1]}), root=root)


def test_resolve_root_env_var(tmp_path, monkeypatch):
    from pathlib import Path

    assert store.resolve_root("explicit") == Path("explicit")
    monkeypatch.setenv("OGPROD_STORE", str(tmp_path / "env-store"))
    assert store.resolve_root(None) == tmp_path / "env-store"
    monkeypatch.delenv("OGPROD_STORE")
    assert store.resolve_root(None) == store.DEFAULT_ROOT
