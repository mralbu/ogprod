"""Tests for ogprod.store (upserts, partition isolation, DuckDB queries, manifest)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyarrow as pa
import shapely

from ogprod import Store, schema
from ogprod.sources import base
from ogprod.store import load_geo, load_production, write_geo, write_production


def _make_table(rows: list[tuple[str, date, float, float]]) -> pa.Table:
    return base.build_production_table(
        source="anp",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series([r[0] for r in rows]),
        entity_name=pd.Series([r[0] for r in rows]),
        period=pd.Series([r[1] for r in rows]),
        field_id=pd.Series(["TUPI"] * len(rows)),
        field_name=pd.Series(["TUPI"] * len(rows)),
        facility_id=pd.Series(["FPSO A"] * len(rows)),
        facility_name=pd.Series(["FPSO A"] * len(rows)),
        basin=pd.Series(["Santos"] * len(rows)),
        region=pd.Series(["Rio de Janeiro"] * len(rows)),
        environment=pd.Series(["offshore"] * len(rows)),
        rates={
            "oil_prod": pd.Series([r[2] for r in rows]),
            "gas_prod": pd.Series([r[3] for r in rows]),
        },
    )


def test_upsert_replaces_restated_month_and_isolates_other_years(tmp_path: Path) -> None:
    initial = _make_table(
        [
            ("W1", date(2025, 1, 1), 1000.0, 0.2),
            ("W1", date(2026, 1, 1), 1100.0, 0.25),
        ]
    )
    parts = write_production(initial, root=tmp_path)
    assert len(parts) == 2

    part_2025 = tmp_path / "store" / "production" / "source=anp" / "year=2025" / "data.parquet"
    bytes_2025_before = part_2025.read_bytes()

    # Restate 2026-01 and add 2026-02.
    revised = _make_table(
        [
            ("W1", date(2026, 1, 1), 1250.0, 0.30),
            ("W1", date(2026, 2, 1), 1200.0, 0.28),
        ]
    )
    rewritten = write_production(revised, root=tmp_path)
    assert len(rewritten) == 1
    assert part_2025.read_bytes() == bytes_2025_before

    df = load_production(source="anp", root=tmp_path)
    assert len(df) == 3
    jan_2026 = df.loc[df["period"] == pd.Timestamp("2026-01-01"), "oil_prod"].iloc[0]
    assert jan_2026 == 1250.0


def test_store_filters_sql_and_geo_coverage(tmp_path: Path) -> None:
    db = Store(tmp_path)
    write_production(
        _make_table([("W1", date(2026, 1, 1), 5000.0, 1.5)]),
        root=tmp_path,
    )
    fac = gpd.GeoDataFrame(
        {
            "source": ["anp"],
            "facility_id": ["FPSO A"],
            "facility_name": ["FPSO A"],
            "oil_capacity_bpd": [180000.0],
        },
        geometry=[shapely.Point(-42.5, -24.5)],
        crs="EPSG:4326",
    )
    write_geo(fac, schema.GeoKind.FACILITIES, root=tmp_path)

    filtered = db.production(source="anp", field_id="TUPI", start="2026-01", end="2026-01")
    assert len(filtered) == 1
    assert filtered["oil_prod"].dtype == "float32"

    assert len(db.production(field_id=[])) == 0
    assert len(load_geo(schema.GeoKind.FACILITIES, source="anp", root=tmp_path)) == 1
    assert len(db.wells()) == 0

    joined = db.sql(
        """
        SELECT p.entity_id, p.oil_prod, f.oil_capacity_bpd
        FROM production p
        JOIN facilities f ON p.source = f.source AND p.facility_id = f.facility_id
        """
    )
    assert len(joined) == 1
    assert joined["oil_capacity_bpd"].iloc[0] == 180000.0

    cov = db.coverage()
    assert cov.loc[0, "source"] == "anp"
    assert cov.loc[0, "rows"] == 1
    assert cov.loc[0, "facilities"] == 1
    assert cov.loc[0, "fields"] == 0
