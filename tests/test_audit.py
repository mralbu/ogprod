"""Tests for ogprod.audit, HTTP retry/truncation hardening, and `ogprod audit` CLI."""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import httpx2
import pandas as pd
import pyarrow.parquet as pq
import pytest
import shapely
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

from ogprod import Store, audit, schema
from ogprod.cli import APP_NAME, cli
from ogprod.sources import base, brazil, mexico
from ogprod.store import write_geo, write_production


@given(
    start_year=st.integers(min_value=1970, max_value=2030),
    span_months=st.integers(min_value=3, max_value=36),
    drop_indices=st.sets(st.integers(min_value=1, max_value=34), max_size=6),
)
def test_missing_months_in_span_exact_complement(
    start_year: int, span_months: int, drop_indices: set[int]
) -> None:
    all_months: list[date] = []
    for offset in range(span_months):
        yr, mo = divmod((start_year * 12) + offset, 12)
        all_months.append(date(yr, mo + 1, 1))
    valid_drops = {i for i in drop_indices if 0 < i < span_months - 1}
    kept = [m for i, m in enumerate(all_months) if i not in valid_drops]
    expected = [f"{all_months[i]:%Y-%m}" for i in sorted(valid_drops)]
    assert audit._missing_months_in_span(kept) == expected


def test_audit_store_clean_and_cli_exit_zero(tmp_path: Path) -> None:
    table = base.build_production_table(
        source="norway",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["1", "1"]),
        entity_name=pd.Series(["TROLL", "TROLL"]),
        period=pd.Series([date(2026, 8, 1), date(2026, 9, 1)]),
        field_id=pd.Series(["1", "1"]),
        field_name=pd.Series(["TROLL", "TROLL"]),
        environment=pd.Series(["offshore", "offshore"]),
        rates={"oil_prod": pd.Series([1000.0, 1100.0]), "gas_prod": pd.Series([500.0, 520.0])},
    )
    write_production(table, root=tmp_path)
    fields = gpd.GeoDataFrame(
        {
            "source": ["norway"],
            "field_id": ["1"],
            "field_name": ["TROLL"],
            "discovery_date": ["1979-01-01"],
        },
        geometry=[shapely.box(3.0, 60.0, 4.0, 61.0)],
        crs="EPSG:4326",
    )
    write_geo(fields, schema.GeoKind.FIELDS, root=tmp_path)

    report = Store(tmp_path).audit(reference_date=date(2026, 10, 1))
    assert report.ok
    assert report.findings == ()

    res = CliRunner().invoke(
        cli,
        ["audit", "--data-dir", str(tmp_path), "--stale-months", "24", "--json"],
        prog_name=APP_NAME,
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["error_count"] == 0


def test_audit_store_detects_corruption_staleness_joins_and_geo_hygiene(tmp_path: Path) -> None:
    # Seed a store with:
    # - zero-hydrocarbon month (brazil 2018-01 onshore)
    # - missing months & env tail lag (brazil 2025 onshore stops at 2025-09 while offshore at 12)
    # - stale feed (reference_date far ahead)
    # - low join coverage (unmatched field & well)
    table = base.build_production_table(
        source="brazil",
        entity_type=schema.EntityType.WELL,
        entity_id=pd.Series(["W1", "W1", "W2", "W2", "W3"]),
        entity_name=pd.Series(["W1", "W1", "W2", "W2", "W3"]),
        period=pd.Series(
            [
                date(2018, 1, 1),
                date(2018, 2, 1),
                date(2025, 1, 1),
                date(2025, 9, 1),
                date(2025, 12, 1),
            ]
        ),
        field_id=pd.Series(["UNMATCHED", "UNMATCHED", "UNMATCHED", "UNMATCHED", "UNMATCHED"]),
        field_name=pd.Series(["UNMATCHED"] * 5),
        environment=pd.Series(["onshore", "onshore", "onshore", "onshore", "offshore"]),
        rates={
            "oil_prod": pd.Series([0.0, 100.0, 80.0, 80.0, 5000.0]),
            "gas_prod": pd.Series([0.0, 10.0, 5.0, 5.0, 200.0]),
        },
    )
    write_production(table, root=tmp_path)

    # Write a raw GeoParquet file bypassing prepare_geo to simulate legacy pre-fix geo issues.
    bad_fields = schema.empty_geo(schema.GeoKind.FIELDS)
    bad_fields.loc[0, ["source", "field_id", "field_name", "discovery_date", "status"]] = [
        "brazil",
        "OTHER",
        "OTHER",
        "12/05/2016",
        "   ",
    ]
    bad_fields.loc[0, "geometry"] = shapely.box(-42.0, -24.0, -41.0, -23.0)
    fields_path = tmp_path / "store" / "geo" / "fields" / "brazil.parquet"
    fields_path.parent.mkdir(parents=True, exist_ok=True)
    bad_fields.to_parquet(fields_path, index=False)

    bad_wells = schema.empty_geo(schema.GeoKind.WELLS)
    bad_wells.loc[0, ["source", "well_id", "well_name", "field_id"]] = [
        "brazil",
        "W-OTHER",
        "W-OTHER",
        "NO DISPONIBLE",
    ]
    bad_wells.loc[0, "geometry"] = shapely.Point(-42.0, -24.0)
    wells_path = tmp_path / "store" / "geo" / "wells" / "brazil.parquet"
    wells_path.parent.mkdir(parents=True, exist_ok=True)
    bad_wells.to_parquet(wells_path, index=False)

    report = audit.audit_store(tmp_path, reference_date=date(2026, 11, 1), stale_months=6)
    checks = {(f.severity, f.check) for f in report.findings}
    assert ("error", "zero_hydrocarbon_month") in checks
    assert ("error", "missing_months") in checks
    assert ("error", "env_tail_lag") in checks
    assert ("error", "geo_dates") in checks
    assert ("error", "geo_whitespace") in checks
    assert ("warning", "stale_feed") in checks
    assert ("warning", "join_coverage") in checks
    assert ("warning", "geo_sentinels") in checks
    assert not report.ok

    # CLI exits 1 when errors exist.
    res = CliRunner().invoke(cli, ["audit", "--data-dir", str(tmp_path)], prog_name=APP_NAME)
    assert res.exit_code == 1
    assert "[error]" in res.stdout


def test_audit_detects_out_of_year_partition_and_strict_warning_exit(tmp_path: Path) -> None:
    table = base.build_production_table(
        source="mexico",
        entity_type=schema.EntityType.FIELD,
        entity_id=pd.Series(["F1"]),
        entity_name=pd.Series(["F1"]),
        period=pd.Series([date(2024, 1, 1)]),
        field_id=pd.Series(["F1"]),
        field_name=pd.Series(["F1"]),
        environment=pd.Series(["onshore"]),
        rates={"oil_prod": pd.Series([500.0])},
    )
    # Write 2024 data cleanly -> stale warning only under --strict.
    write_production(table, root=tmp_path)
    res_warn = CliRunner().invoke(
        cli,
        ["audit", "--data-dir", str(tmp_path), "--stale-months", "1"],
        prog_name=APP_NAME,
    )
    assert res_warn.exit_code == 0
    res_strict = CliRunner().invoke(
        cli,
        ["audit", "--data-dir", str(tmp_path), "--stale-months", "1", "--strict"],
        prog_name=APP_NAME,
    )
    assert res_strict.exit_code == 1

    # Misplace 2024 row into year=2025 partition -> partition error.
    wrong_path = tmp_path / "store" / "production" / "source=mexico" / "year=2025" / "data.parquet"
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, wrong_path)
    report = audit.audit_store(tmp_path, reference_date=date(2025, 2, 1))
    assert any(
        f.check == "partition" and "out-of-partition years" in f.message for f in report.errors
    )


def test_http_retry_and_truncated_content_length_recovery(tmp_path: Path) -> None:
    calls = {"get": 0, "dl": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url.endswith("/flaky"):
            calls["get"] += 1
            if calls["get"] == 1:
                return httpx2.Response(503, text="busy")
            return httpx2.Response(200, text="ok")
        if url.endswith("/not-found"):
            return httpx2.Response(404, text="missing")
        if url.endswith("/truncated"):
            calls["dl"] += 1
            if calls["dl"] == 1:
                return httpx2.Response(200, content=b"short", headers={"Content-Length": "100"})
            return httpx2.Response(200, content=b"complete", headers={"Content-Length": "8"})
        return httpx2.Response(404)

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    resp = base.get_with_retry(client, "https://example.test/flaky", retries=3, backoff_s=0.0)
    assert resp.text == "ok"
    assert calls["get"] == 2

    with pytest.raises(httpx2.HTTPStatusError):
        base.get_with_retry(client, "https://example.test/not-found", retries=3, backoff_s=0.0)

    dest = tmp_path / "file.bin"
    dl = base.download_file(
        client, "https://example.test/truncated", dest, retries=3, backoff_s=0.0
    )
    assert dl.size_bytes == 8
    assert dest.read_bytes() == b"complete"
    assert calls["dl"] == 2
    assert not dest.with_name(f"{dest.name}.part").exists()


def test_parsers_reject_non_empty_csv_collapsing_to_zero_rows() -> None:
    br_null_wells = (
        "Ano,Mês/Ano,Estado,Bacia,Campo,Poço,Ambiente,Instalação,Produção de Óleo (m³)\n"
        "2026,01/2026,Bahia,Recôncavo,CAMPO A,,Terra,Não Informado,10\n"
    )
    with pytest.raises(ValueError, match="0 valid well/period rows"):
        brazil.Brazil().parse_production(io.BytesIO(br_null_wells.encode("utf-8")))

    mx_null_fields = (
        "FECHA,CAMPO_OFICIAL,CAMPO_SIH,UBICACION,PETROLEO_MBD\n01/2025,,,Terrestre,10.0\n"
    )
    with pytest.raises(ValueError, match="0 valid field/period rows"):
        mexico.Mexico().parse_production(io.BytesIO(mx_null_fields.encode("utf-8")))
