"""Store-level data quality auditing for ogprod Layer-1 Parquet & GeoParquet tables.

Runs baseline-free structural, temporal, spatial, join-coverage, and freshness checks
over a local :class:`ogprod.Store` so silent drops, stale regulator feeds, non-ISO
dates, and join regressions are surfaced immediately in both CLI and CI.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import duckdb
import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq

from ogprod import schema, store

type Severity = Literal["error", "warning"]

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_GEO_DATE_COLUMNS: dict[schema.GeoKind, tuple[str, ...]] = {
    schema.GeoKind.FIELDS: ("discovery_date", "production_start_date"),
    schema.GeoKind.FACILITIES: (),
    schema.GeoKind.WELLS: ("spud_date", "completion_date", "production_start", "production_end"),
}
_GEO_KEY_SENTINELS = frozenset({"NO DISPONIBLE", "NAO INFORMADO", "(NAO CADASTRADO)"})
_MIN_FIELD_OIL_JOIN_PCT = 90.0
_MIN_WELL_OIL_JOIN_PCT = 95.0


@dataclass(frozen=True)
class AuditFinding:
    """One data-quality finding emitted by :func:`audit_store`."""

    severity: Severity
    source: str
    check: str
    message: str


@dataclass(frozen=True)
class AuditReport:
    """Structured result of auditing one or more sources in a local store."""

    sources: tuple[str, ...]
    findings: tuple[AuditFinding, ...]

    @property
    def errors(self) -> tuple[AuditFinding, ...]:
        """Error-level findings indicating data corruption or severe loss."""
        return tuple(f for f in self.findings if f.severity == "error")

    @property
    def warnings(self) -> tuple[AuditFinding, ...]:
        """Warning-level findings indicating staleness, upstream gaps, or low join coverage."""
        return tuple(f for f in self.findings if f.severity == "warning")

    @property
    def ok(self) -> bool:
        """True when no error-level findings were detected."""
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the report to a JSON-friendly dictionary."""
        return {
            "ok": self.ok,
            "sources": list(self.sources),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "findings": [asdict(f) for f in self.findings],
        }

    def format_text(self) -> str:
        """Render a concise human-readable audit summary."""
        if not self.sources:
            return "Store is empty (no sources to audit)."
        lines = [
            f"Audited {len(self.sources)} source(s): {', '.join(self.sources)} "
            f"({len(self.errors)} error(s), {len(self.warnings)} warning(s))"
        ]
        for item in self.findings:
            lines.append(f"[{item.severity}] {item.source} ({item.check}): {item.message}")
        return "\n".join(lines)


def _month_index(d: date) -> int:
    return d.year * 12 + d.month


def _discover_store_sources(root: Path, source: str | None) -> list[str]:
    if source is not None:
        return [source]
    found: set[str] = set()
    prod_root = store.store_dir(root) / "production"
    if prod_root.exists():
        for src_dir in prod_root.glob("source=*"):
            found.add(src_dir.name.split("=", 1)[1])
    geo_root = store.store_dir(root) / "geo"
    if geo_root.exists():
        for kind in schema.GeoKind:
            kind_dir = geo_root / kind.value
            if kind_dir.exists():
                for path in kind_dir.glob("*.parquet"):
                    found.add(path.stem)
    return sorted(found)


def _audit_one_partition(path: Path, src: str, year: int) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    try:
        table = pq.read_table(path)
        if table.num_rows == 0:
            findings.append(
                AuditFinding("error", src, "partition", f"year={year}: empty production partition")
            )
            return findings
        schema.validate_production(table)
    except Exception as err:
        findings.append(
            AuditFinding("error", src, "partition", f"year={year}: invalid partition ({err})")
        )
        return findings

    seen_sources = set(table.column("source").unique().to_pylist())
    if seen_sources != {src}:
        findings.append(
            AuditFinding(
                "error",
                src,
                "partition",
                f"year={year}: unexpected source values {sorted(seen_sources)}",
            )
        )
    seen_years = {p.year for p in table.column("period").unique().to_pylist()}
    if seen_years != {year}:
        findings.append(
            AuditFinding(
                "error",
                src,
                "partition",
                f"year={year}: contains out-of-partition years {sorted(seen_years)}",
            )
        )
    return findings


def _audit_production_partitions(root: Path, selected: list[str]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    prod_root = store.store_dir(root) / "production"
    for src in selected:
        src_dir = prod_root / f"source={src}"
        if not src_dir.exists():
            continue
        for yr_dir in sorted(src_dir.glob("year=*")):
            data_file = yr_dir / "data.parquet"
            if not data_file.exists():
                continue
            year = int(yr_dir.name.split("=", 1)[1])
            findings.extend(_audit_one_partition(data_file, src, year))
    return findings


def _missing_months_in_span(periods: list[date]) -> list[str]:
    if len(periods) <= 1:
        return []
    first, last = min(periods), max(periods)
    present = {(p.year, p.month) for p in periods}
    missing: list[str] = []
    cur = _month_index(first)
    end = _month_index(last)
    while cur <= end:
        yr, mo = divmod(cur - 1, 12)
        mo += 1
        if (yr, mo) not in present:
            missing.append(f"{yr:04d}-{mo:02d}")
        cur += 1
    return missing


def _to_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    ts = pd.Timestamp(value)
    assert isinstance(ts, pd.Timestamp)
    return ts.date()


def _audit_env_series(
    src: str, env_label: str, grp: pd.DataFrame, source_last: date
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    periods = [_to_date(p) for p in grp["period"].tolist()]
    missing = _missing_months_in_span(periods)
    if missing:
        sev: Severity = "error" if len(missing) >= 3 else "warning"
        sample = ", ".join(missing[:6]) + ("..." if len(missing) > 6 else "")
        findings.append(
            AuditFinding(
                sev,
                src,
                "missing_months",
                f"{env_label}: {len(missing)} missing month(s) between "
                f"{min(periods):%Y-%m} and {max(periods):%Y-%m}: {sample}",
            )
        )

    env_last = max(periods)
    lag = _month_index(source_last) - _month_index(env_last)
    if lag >= 3:
        findings.append(
            AuditFinding(
                "error",
                src,
                "env_tail_lag",
                f"{env_label}: ends {lag} month(s) before source last_period "
                f"({env_last:%Y-%m} vs {source_last:%Y-%m})",
            )
        )

    grp_years = grp.assign(yr=pd.to_datetime(grp["period"]).dt.year)
    for yr, ygrp in grp_years.groupby("yr", sort=True):
        has_pos = bool(((ygrp["oil"] > 0.0) | (ygrp["gas"] > 0.0)).any())
        if not has_pos:
            continue
        zeros = ygrp.loc[(ygrp["oil"] == 0.0) & (ygrp["gas"] == 0.0), "period"]
        if len(zeros) > 0:
            findings.append(
                AuditFinding(
                    "error",
                    src,
                    "zero_hydrocarbon_month",
                    f"{env_label}/{yr}: {len(zeros)} month(s) with zero oil and gas "
                    "in an active producing year",
                )
            )
    return findings


def _audit_monthly_continuity(
    con: duckdb.DuckDBPyConnection, selected: list[str]
) -> list[AuditFinding]:
    df = con.execute(
        """
        SELECT source,
               coalesce(environment, 'unspecified') AS env,
               period,
               sum(coalesce(CAST(oil_prod AS DOUBLE), 0.0)) AS oil,
               sum(coalesce(CAST(gas_prod AS DOUBLE), 0.0)) AS gas
        FROM production
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
        """
    ).df()
    if df.empty:
        return []
    selected_set = set(selected)
    findings: list[AuditFinding] = []
    for src_val, src_grp in df.groupby("source", sort=True):
        src = str(src_val)
        if src not in selected_set:
            continue
        source_last = _to_date(src_grp["period"].max())
        for env_val, env_grp in src_grp.groupby("env", sort=True):
            findings.extend(_audit_env_series(src, str(env_val), env_grp, source_last))
    return findings


def _audit_staleness(
    con: duckdb.DuckDBPyConnection,
    selected: list[str],
    *,
    reference_date: date,
    stale_months: int,
) -> list[AuditFinding]:
    df = con.execute("SELECT source, max(period) AS last_p FROM production GROUP BY 1").df()
    if df.empty:
        return []
    selected_set = set(selected)
    ref_idx = _month_index(reference_date)
    findings: list[AuditFinding] = []
    for rec in df.to_dict("records"):
        src = str(rec["source"])
        if src not in selected_set or pd.isna(rec["last_p"]):
            continue
        last_d = _to_date(rec["last_p"])
        lag = ref_idx - _month_index(last_d)
        if lag > stale_months:
            findings.append(
                AuditFinding(
                    "warning",
                    src,
                    "stale_feed",
                    f"latest period {last_d:%Y-%m} is {lag} month(s) behind {reference_date:%Y-%m}",
                )
            )
    return findings


def _audit_join_coverage(con: duckdb.DuckDBPyConnection, selected: list[str]) -> list[AuditFinding]:
    selected_set = set(selected)
    findings: list[AuditFinding] = []

    field_df = con.execute(
        """
        WITH f_cnt AS (SELECT source, count(*) AS n FROM fields GROUP BY 1),
             p AS (
                 SELECT p.source,
                        sum(coalesce(CAST(p.oil_prod AS DOUBLE), 0.0)) AS total_oil,
                        sum(CASE WHEN f.field_id IS NOT NULL
                                 THEN coalesce(CAST(p.oil_prod AS DOUBLE), 0.0) ELSE 0.0 END)
                            AS matched_oil
                 FROM production p
                 LEFT JOIN fields f ON p.source = f.source AND p.field_id = f.field_id
                 WHERE p.field_id <> ''
                 GROUP BY 1
             )
        SELECT p.source, f_cnt.n AS geo_rows, p.total_oil, p.matched_oil
        FROM p JOIN f_cnt ON p.source = f_cnt.source
        WHERE f_cnt.n > 0 AND p.total_oil > 0
        """
    ).df()
    for rec in field_df.to_dict("records"):
        src = str(rec["source"])
        if src not in selected_set:
            continue
        pct = 100.0 * float(rec["matched_oil"]) / float(rec["total_oil"])
        if pct < _MIN_FIELD_OIL_JOIN_PCT:
            findings.append(
                AuditFinding(
                    "warning",
                    src,
                    "join_coverage",
                    f"production->fields oil match is {pct:.1f}% "
                    f"(< {_MIN_FIELD_OIL_JOIN_PCT:.0f}%)",
                )
            )

    well_df = con.execute(
        """
        WITH w_cnt AS (SELECT source, count(*) AS n FROM wells GROUP BY 1),
             p AS (
                 SELECT p.source,
                        sum(coalesce(CAST(p.oil_prod AS DOUBLE), 0.0)) AS total_oil,
                        sum(CASE WHEN w.well_id IS NOT NULL
                                 THEN coalesce(CAST(p.oil_prod AS DOUBLE), 0.0) ELSE 0.0 END)
                            AS matched_oil
                 FROM production p
                 LEFT JOIN wells w ON p.source = w.source AND p.entity_id = w.well_id
                 WHERE p.entity_type = 'well'
                 GROUP BY 1
             )
        SELECT p.source, w_cnt.n AS geo_rows, p.total_oil, p.matched_oil
        FROM p JOIN w_cnt ON p.source = w_cnt.source
        WHERE w_cnt.n > 0 AND p.total_oil > 0
        """
    ).df()
    for rec in well_df.to_dict("records"):
        src = str(rec["source"])
        if src not in selected_set:
            continue
        pct = 100.0 * float(rec["matched_oil"]) / float(rec["total_oil"])
        if pct < _MIN_WELL_OIL_JOIN_PCT:
            findings.append(
                AuditFinding(
                    "warning",
                    src,
                    "join_coverage",
                    f"production->wells oil match is {pct:.1f}% (< {_MIN_WELL_OIL_JOIN_PCT:.0f}%)",
                )
            )
    return findings


def _audit_geo_string_columns(
    gdf: gpd.GeoDataFrame, src: str, kind: schema.GeoKind
) -> list[AuditFinding]:
    spec = schema.GEO_SPECS[kind]
    findings: list[AuditFinding] = []
    numeric_cols = set(spec.float32_columns) | set(spec.int32_columns)
    for col in spec.columns:
        if col == "geometry" or col in numeric_cols:
            continue
        s = gdf[col].dropna().astype(str)
        if s.empty:
            continue
        ws_count = int((s.str.strip() == "").sum())
        if ws_count > 0:
            findings.append(
                AuditFinding(
                    "error",
                    src,
                    "geo_whitespace",
                    f"{kind.value}.{col}: {ws_count} whitespace-only string(s)",
                )
            )
        if col in _GEO_DATE_COLUMNS[kind]:
            bad_dates = int((~s.str.match(_ISO_DATE_RE)).sum())
            if bad_dates > 0:
                findings.append(
                    AuditFinding(
                        "error",
                        src,
                        "geo_dates",
                        f"{kind.value}.{col}: {bad_dates}/{len(s)} non-ISO date value(s)",
                    )
                )
        if col.endswith("_id"):
            sent_count = int(s.map(schema.normalize_key).isin(_GEO_KEY_SENTINELS).sum())
            if sent_count > 0:
                findings.append(
                    AuditFinding(
                        "warning",
                        src,
                        "geo_sentinels",
                        f"{kind.value}.{col}: {sent_count} placeholder sentinel value(s)",
                    )
                )
    return findings


def _audit_geo_tables(root: Path, selected: list[str]) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    geo_root = store.store_dir(root) / "geo"
    for kind in schema.GeoKind:
        for src in selected:
            path = geo_root / kind.value / f"{src}.parquet"
            if not path.exists():
                continue
            try:
                gdf = gpd.read_parquet(path)
                schema.validate_geo(gdf, kind)
            except Exception as err:
                findings.append(
                    AuditFinding("error", src, "geo_schema", f"{kind.value}: invalid ({err})")
                )
                continue
            findings.extend(_audit_geo_string_columns(gdf, src, kind))
    return findings


def audit_store(
    root: str | Path | None = None,
    *,
    source: str | None = None,
    reference_date: date | None = None,
    stale_months: int = 6,
) -> AuditReport:
    """Run structural, continuity, staleness, join, and geo checks over a local store."""
    base_root = store.resolve_root(root)
    selected = _discover_store_sources(base_root, source)
    if not selected:
        return AuditReport(sources=(), findings=())

    ref_date = reference_date or datetime.now(tz=UTC).date()
    findings: list[AuditFinding] = []
    findings.extend(_audit_production_partitions(base_root, selected))
    findings.extend(_audit_geo_tables(base_root, selected))

    with duckdb.connect() as con:
        store._register_store_views(con, base_root)
        findings.extend(_audit_monthly_continuity(con, selected))
        findings.extend(
            _audit_staleness(con, selected, reference_date=ref_date, stale_months=stale_months)
        )
        findings.extend(_audit_join_coverage(con, selected))

    return AuditReport(sources=tuple(selected), findings=tuple(findings))
