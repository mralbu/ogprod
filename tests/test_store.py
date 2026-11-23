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
        source="brazil",
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

    part_2025 = tmp_path / "store" / "production" / "source=brazil" / "year=2025" / "data.parquet"
    bytes_2025_before = part_2025.read_bytes()

    revised = _make_table(
        [
            ("W1", date(2026, 1, 1), 1250.0, 0.30),
            ("W1", date(2026, 2, 1), 1200.0, 0.28),
        ]
    )
    rewritten = write_production(revised, root=tmp_path)
    assert len(rewritten) == 1
    assert part_2025.read_bytes() == bytes_2025_before

    df = load_production(source="brazil", root=tmp_path)
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
            "source": ["brazil"],
            "facility_id": ["FPSO A"],
            "facility_name": ["FPSO A"],
            "oil_capacity_bpd": [180000.0],
        },
        geometry=[shapely.Point(-42.5, -24.5)],
        crs="EPSG:4326",
    )
    write_geo(fac, schema.GeoKind.FACILITIES, root=tmp_path)

    filtered = db.production(source="brazil", field_id="TUPI", start="2026-01", end="2026-01")
    assert len(filtered) == 1
    assert filtered["oil_prod"].dtype == "float32"

    assert len(db.production(field_id=[])) == 0
    assert len(load_geo(schema.GeoKind.FACILITIES, source="brazil", root=tmp_path)) == 1
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
    assert cov.loc[0, "source"] == "brazil"
    assert cov.loc[0, "rows"] == 1
    assert cov.loc[0, "facilities"] == 1
    assert cov.loc[0, "fields"] == 0


def test_refresh_replaces_requested_year_without_wiping_spillover_or_other_env(
    tmp_path: Path,
) -> None:
    import httpx2

    seed = base.build_production_table(
        source="brazil",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["W-ON-17", "W-STALE-18"]),
        entity_name=pd.Series(["W-ON-17", "W-STALE-18"]),
        period=pd.Series([date(2017, 1, 1), date(2018, 1, 1)]),
        field_id=pd.Series(["F1", "F1"]),
        field_name=pd.Series(["F1", "F1"]),
        facility_id=pd.Series(["", "OLD STALE FAC"]),
        facility_name=pd.Series([pd.NA, "OLD STALE FAC"]),
        environment=pd.Series(["onshore", "offshore"]),
        rates={"oil_prod": pd.Series([100.0, 999.0])},
    )
    write_production(seed, root=tmp_path)

    portal_html = (
        '<a href="https://www.gov.br/anp/pt-br/centrais-de-conteudo/dados-abertos/arquivos/'
        "arquivos-fase-de-desenvolvimento-e-producao/"
        'arquivos-producao-de-petroleo-e-gas-natural-nacional/pm/producao-mar-2017-2018.csv">m</a>'
    )
    csv_body = (
        (
            "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,Produção de Óleo (m³)\n"
            "2017,01/2017,Rio de Janeiro,Santos,F1,W-OFF-17,Mar,FPSO A,158,99\n"
            "2018,01/2018,Rio de Janeiro,Santos,F1,W-FRESH-18,Mar,FPSO A,317,97\n"
        )
        .replace("158,99", '"158,99"')
        .replace("317,97", '"317,97"')
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("fase-de-desenvolvimento-e-producao"):
            return httpx2.Response(200, text=portal_html)
        if url.endswith("producao-mar-2017-2018.csv"):
            return httpx2.Response(200, text=csv_body)
        return httpx2.Response(404)

    db = Store(tmp_path)
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    db.ingest(
        "brazil", year=2018, origin="publisher", refresh=True, geo_layers=False, client=client
    )

    df_2018 = db.production(source="brazil", start="2018-01", end="2018-12")
    assert list(df_2018["entity_id"]) == ["W-FRESH-18"]

    df_2017 = db.production(source="brazil", start="2017-01", end="2017-12")
    assert set(df_2017["entity_id"]) == {"W-ON-17", "W-OFF-17"}
