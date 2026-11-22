import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

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


def test_duplicate_keys_within_one_write_rejected(root):
    # Two rows a parser can't tell apart must not silently collapse to one.
    frame = pd.concat(
        [production_frame([{"volume": 1.0}]), production_frame([{"volume": 7.0}])],
        ignore_index=True,
    )
    with pytest.raises(schema.SchemaError, match="duplicate"):
        store.write_production(frame, root=root)
    assert not root.exists()


def test_combine_restatements_later_frame_wins():
    # ANP's pré-sal file restates a subset of the mar file: across files the
    # later row replaces the earlier one; unrelated rows are all kept.
    mar = production_frame([{"entity_id": "W-1", "volume": 1.0}, {"entity_id": "W-2"}])
    pre_sal = production_frame([{"entity_id": "W-1", "volume": 7.0}])
    combined = store.combine_restatements([mar, pre_sal])
    assert combined.set_index("entity_id")["volume"].to_dict() == {"W-1": 7.0, "W-2": 100.0}


def test_combine_restatements_rejects_duplicates_within_one_frame():
    repeated = pd.concat([production_frame([{}]), production_frame([{}])], ignore_index=True)
    with pytest.raises(schema.SchemaError, match="duplicate"):
        store.combine_restatements([repeated])


def test_one_well_split_across_fields_keeps_both_rows(root):
    # ANP reports a single well's month under several fields (e.g. ATAPU and
    # ATAPU_ECO); those are distinct volumes, so field is part of the key.
    frame = production_frame(
        [{"field": "ATAPU", "volume": 3.0}, {"field": "ATAPU_ECO", "volume": 4.0}]
    )
    store.write_production(frame, root=root)
    store.write_production(production_frame([{"field": "ATAPU", "volume": 5.0}]), root=root)
    stored = pd.read_parquet(root / "production/source=testsrc/year=2023/data.parquet")
    assert stored.set_index("field")["volume"].to_dict() == {"ATAPU": 5.0, "ATAPU_ECO": 4.0}


def test_null_field_rows_still_upsert(root):
    store.write_production(production_frame([{"field": None, "volume": 1.0}]), root=root)
    store.write_production(production_frame([{"field": None, "volume": 2.0}]), root=root)
    stored = pd.read_parquet(root / "production/source=testsrc/year=2023/data.parquet")
    assert len(stored) == 1
    assert stored["volume"].iloc[0] == 2.0


def test_write_rejects_invalid_frame(root):
    with pytest.raises(schema.SchemaError):
        store.write_production(pd.DataFrame({"nope": [1]}), root=root)


def test_resolve_root_order(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("OGPROD_STORE", str(tmp_path / "env-store"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert store.resolve_root("explicit") == Path("explicit")
    assert store.resolve_root(None) == tmp_path / "env-store"
    monkeypatch.delenv("OGPROD_STORE")
    assert store.resolve_root(None) == tmp_path / "xdg/ogprod/store"
    monkeypatch.delenv("XDG_DATA_HOME")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert store.resolve_root(None) == tmp_path / "home/.local/share/ogprod/store"


def entities_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "source": ["testsrc", "testsrc", "othersrc"],
            "entity_type": ["well", "well", "field"],
            "entity_id": ["W-1", "W-2", "F-1"],
            "entity_name": ["W-1", "W-2", "FIELD ONE"],
            "field": ["F", "F", "FIELD ONE"],
            "basin": [None, None, None],
            "region": ["R", "R", "R"],
            "operator": ["OP", "OP", "OP"],
            "status": ["Producing", "Abandoned", "Producing"],
            "geometry": [
                Point(-40.0, -22.0),
                Point(-40.1, -22.1),
                Polygon([(3.2, 56.5), (3.3, 56.5), (3.3, 56.6)]),
            ],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def test_entities_round_trip(root):
    written = store.write_entities(entities_frame(), root=root)
    assert sorted(p.name for p in written) == ["othersrc.parquet", "testsrc.parquet"]
    loaded = store.load_entities(root=root)
    schema.validate_entities(loaded)
    assert len(loaded) == 3
    assert set(loaded["source"]) == {"testsrc", "othersrc"}


def test_load_entities_filters(root):
    store.write_entities(entities_frame(), root=root)
    wells = store.load_entities(source="testsrc", entity_type="well", root=root)
    assert set(wells["entity_id"]) == {"W-1", "W-2"}
    assert store.load_entities(source="missing", root=root).empty


def test_write_entities_replaces_per_source(root):
    store.write_entities(entities_frame(), root=root)
    smaller = entities_frame().iloc[[0]]  # testsrc shrinks to one well
    store.write_entities(smaller, root=root)
    assert len(store.load_entities(source="testsrc", root=root)) == 1
    assert len(store.load_entities(source="othersrc", root=root)) == 1  # untouched


def test_load_entities_empty_store(root):
    loaded = store.load_entities(root=root)
    assert loaded.empty
    assert list(loaded.columns) == list(schema.ENTITY_COLUMNS)


def test_load_production_round_trip(root):
    frame = production_frame(
        [
            {"entity_id": "W-1", "product": "oil", "volume": 1.0},
            {"entity_id": "W-1", "product": "gas", "volume": 2.0},
            {"entity_id": "W-2", "product": "oil", "volume": 3.0},
        ]
    )
    store.write_production(frame, root=root)
    loaded = store.load_production(root=root)
    schema.validate(loaded)
    expected = frame.sort_values(["source", "entity_id", "period", "product"], ignore_index=True)
    pd.testing.assert_frame_equal(
        loaded.reset_index(drop=True), expected, check_dtype=False, check_exact=False
    )


def test_load_production_filters(root):
    store.write_production(
        production_frame(
            [
                {"entity_id": "W-1", "product": "oil", "period": pd.Timestamp("2022-06-01")},
                {"entity_id": "W-1", "product": "oil", "period": pd.Timestamp("2023-06-01")},
                {"entity_id": "W-2", "product": "gas", "period": pd.Timestamp("2023-06-01")},
            ]
        ),
        root=root,
    )
    by_product = store.load_production(product="gas", root=root)
    assert set(by_product["entity_id"]) == {"W-2"}

    by_period = store.load_production(start="2023-01-01", root=root)
    assert set(by_period["period"]) == {pd.Timestamp("2023-06-01")}

    by_list = store.load_production(entity_id=["W-1", "W-2"], end="2022-12-31", root=root)
    assert len(by_list) == 1

    assert store.load_production(source="missing", root=root).empty


def test_load_production_empty_store(root):
    loaded = store.load_production(root=root)
    assert loaded.empty
    assert list(loaded.columns) == list(schema.CANONICAL_COLUMNS)


def test_sql_joins_production_and_entities(root):
    store.write_production(production_frame([{"entity_id": "W-1", "volume": 42.0}]), root=root)
    store.write_entities(entities_frame(), root=root)
    result = store.sql(
        """
        SELECT p.entity_id, p.volume, e.status
        FROM production p JOIN entities e
          ON p.source = e.source AND p.entity_id = e.entity_id
        """,
        root=root,
    )
    assert result.iloc[0].to_dict() == {"entity_id": "W-1", "volume": 42.0, "status": "Producing"}


def test_sql_on_empty_store(root):
    assert store.sql("SELECT count(*) AS n FROM production", root=root)["n"].iloc[0] == 0


def test_load_production_empty_filter_list(root):
    store.write_production(production_frame([{}]), root=root)
    loaded = store.load_production(entity_id=[], root=root)
    assert loaded.empty
    assert list(loaded.columns) == list(schema.CANONICAL_COLUMNS)


def test_sql_empty_store_entities_has_geometry_column(root):
    result = store.sql("SELECT geometry FROM entities", root=root)
    assert len(result) == 0


def test_sql_entities_geometry_typed_blob_on_empty_store(root):
    # The empty-store fallback view must expose the same column types as a
    # populated store (geometry = WKB bytes), so queries behave identically.
    described = store.sql(
        "SELECT column_type FROM (DESCRIBE entities) WHERE column_name = 'geometry'", root=root
    )
    assert described["column_type"].iloc[0] == "BLOB"


def test_parquet_uses_zstd_and_bounded_row_groups(root, monkeypatch):
    import pyarrow.parquet as pq

    assert store._PRODUCTION_ROW_GROUP_SIZE == 128_000
    monkeypatch.setattr(store, "_PRODUCTION_ROW_GROUP_SIZE", 10)
    frame = production_frame([{"entity_id": f"W-{i:03d}", "volume": float(i)} for i in range(25)])
    (prod_path,) = store.write_production(frame, root=root)

    prod_meta = pq.ParquetFile(prod_path).metadata
    assert prod_meta.num_row_groups == 3
    for rg_idx in range(prod_meta.num_row_groups):
        rg = prod_meta.row_group(rg_idx)
        assert rg.num_rows <= 10
        for col_idx in range(rg.num_columns):
            assert rg.column(col_idx).compression == "ZSTD"

    entity_paths = store.write_entities(entities_frame(), root=root)
    for ent_path in entity_paths:
        ent_meta = pq.ParquetFile(ent_path).metadata
        for col_idx in range(ent_meta.row_group(0).num_columns):
            assert ent_meta.row_group(0).column(col_idx).compression == "ZSTD"
