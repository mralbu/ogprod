"""Generate ``llms.txt`` for the static Web Explorer and Layer-2 data export.

Renders a concise, machine- and LLM-readable guide following the ``llms.txt``
convention so both web-fetch-only chat models and code/DuckDB-capable agents
can discover and query the exported JSON, Parquet, and Hugging Face mirror
assets directly without executing the client-side SPA.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from ogprod import schema

_EXPLORER_PRODUCTS_CSV = (
    "oil_prod,gas_prod,water_prod,condensate_prod,ngl_prod,water_inj,gas_inj,co2_inj,gor,water_cut"
)


def normalize_site_url(site_url: str | None) -> str:
    """Normalize an optional public Explorer base URL to end with ``/`` (or ``""``)."""
    if site_url is None:
        return ""
    cleaned = site_url.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        raise ValueError(f"site_url must start with http:// or https://, got {site_url!r}")
    return cleaned.rstrip("/") + "/"


def format_byte_size(num_bytes: int) -> str:
    """Format a non-negative byte count as a compact human-readable string."""
    if num_bytes < 0:
        raise ValueError(f"num_bytes must be non-negative, got {num_bytes}")
    if num_bytes < 1024:
        return f"{num_bytes} B"
    kb = num_bytes / 1024
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024
    return f"{mb:.1f} MB"


def collect_asset_sizes(out_dir: Path) -> dict[str, int]:
    """Return ``{relative_posix_path: size_bytes}`` for every file under ``out_dir``."""
    if not out_dir.exists():
        return {}
    return {
        path.relative_to(out_dir).as_posix(): path.stat().st_size
        for path in sorted(out_dir.rglob("*"))
        if path.is_file()
    }


def _data_href(base: str, rel_path: str) -> str:
    return f"{base}data/{rel_path.lstrip('/')}"


def _endpoint_bullet(
    label: str,
    rel_path: str,
    description: str,
    *,
    base: str,
    sizes: Mapping[str, int],
) -> str:
    href = _data_href(base, rel_path)
    size = sizes.get(rel_path)
    size_tag = f" ({format_byte_size(size)})" if size is not None else ""
    return f"- [{label}]({href}){size_tag}: {description}"


def _fmt_number(value: Any, *, digits: int = 0) -> str:
    if value is None:
        return "—"
    if digits == 0:
        return f"{round(float(value)):,}"
    return f"{float(value):,.{digits}f}"


def _period_span(src: Mapping[str, Any]) -> str:
    first = src.get("first_period")
    last = src.get("last_period")
    if first and last:
        return f"{str(first)[:7]} to {str(last)[:7]}"
    years = src.get("years") or []
    if years:
        return f"{years[0]} to {years[-1]}"
    return "—"


def _coverage_section(sources_list: list[dict[str, Any]]) -> list[str]:
    lines = ["## Current Coverage", ""]
    if not sources_list:
        lines.append("No sources are currently present in this export.")
        return lines

    lines.extend(
        [
            "| Source | Country | Publisher | Grain | Period | Rows | Fields | Facilities"
            " | Wells | Latest Oil (bbl/d) | Latest Gas (e3m3/d) |",
            "|---|---|---|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for src in sources_list:
        counts = src.get("counts") or {}
        lines.append(
            f"| `{src['id']}` | {src.get('country', src['id'])} | {src.get('publisher', '')}"
            f" | {src.get('entity_type', 'field')} | {_period_span(src)}"
            f" | {_fmt_number(counts.get('production_rows'))}"
            f" | {_fmt_number(counts.get('fields'))}"
            f" | {_fmt_number(counts.get('facilities'))}"
            f" | {_fmt_number(counts.get('wells'))}"
            f" | {_fmt_number(src.get('latest_oil_bpd'))}"
            f" | {_fmt_number(src.get('latest_gas_e3m3d'), digits=1)} |"
        )

    lines.extend(["", "Available rate columns per source:"])
    for src in sources_list:
        prods = src.get("available_products") or []
        prod_str = ", ".join(f"`{p}`" for p in prods) if prods else "none"
        lines.append(f"- `{src['id']}`: {prod_str}")
    return lines


def _known_gaps_section(sources_list: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for src in sources_list:
        for gap in src.get("known_gaps") or []:
            resolved = gap.get("resolved_in_country_rollup")
            rollup_src = gap.get("country_rollup_source")
            note = ""
            if resolved:
                src_note = f" via {rollup_src}" if rollup_src else ""
                note = (
                    f" (Backfilled in `country_monthly`{src_note}; masked to `NULL` in"
                    " `basin_monthly`; reported values preserved at field/facility/well level.)"
                )
            items.append(
                f"- `{src['id']}` (`{gap['column']}`, {gap['start']} to {gap['end']}):"
                f" {gap['description']}{note}"
            )
    if not items:
        return []
    return ["### Known Data Gaps", "", *items]


def _units_section(sources_list: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Units & Data Conventions",
        "",
        "All production and injection rates are **calendar-day monthly averages**"
        " (`monthly_volume / days_in_month`); `period` (`p` in compact JSON) is always"
        " the first day of the month (`YYYY-MM-DD`).",
        "",
        "- **Liquids** (`oil_prod`, `condensate_prod`, `ngl_prod`, `water_prod`, `water_inj`):"
        " **`bbl/d`**",
        "- **Gases** (`gas_prod`, `gas_inj`, `co2_inj`): **`e3m3/d`** (`10³ m³/d`,"
        " **thousand** m³/day — not million m³/d; multiply by `0.0353147` for `MMscf/d`"
        " or `1000` for `m³/d`).",
        "- **Derived metrics:** `cum_oil_mmbbl` is cumulative oil in million barrels (`MMbbl`);"
        " `oil_utilization` (`oil_util` in monthly JSON) and `gas_utilization` are ratios"
        " `actual / nameplate_capacity` (e.g. `0.85` = 85%).",
        "- **Missing vs zero:** in Parquet, `NULL` means unreported and `0.0` means reported"
        " zero; in compact monthly JSON (`rollups/*.json`), both `NULL` and `0.0` keys are"
        " omitted.",
    ]
    gaps = _known_gaps_section(sources_list)
    if gaps:
        lines.extend(["", *gaps])
    return lines


def _lightweight_json_section(*, base: str, sizes: Mapping[str, int]) -> list[str]:
    return [
        "## Lightweight JSON Endpoints (Safe for Direct HTTP Fetch)",
        "",
        _endpoint_bullet(
            "meta.json",
            "meta.json",
            "Machine-readable catalog: sources, bboxes, years, products, counts, mirror URL,"
            " units, and `known_gaps`.",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/country_monthly.json",
            "rollups/country_monthly.json",
            "Monthly rate series keyed by `{source: [{p, oil_prod, gas_prod, ...}]}`.",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/fields_summary.json",
            "rollups/fields_summary.json",
            "All fields sorted by `latest_oil_bpd` desc (`source`, `field_id`, `field_name`,"
            " `basin`, `region`, `environment`, `operator`, `status`, `water_depth_m`,"
            " `area_km2`, `first_year`, `last_year`, `latest_period`, `cum_oil_mmbbl`,"
            " `latest_oil_bpd`, `latest_gas_e3m3d`, `lon`, `lat`).",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/facilities_summary.json",
            "rollups/facilities_summary.json",
            "All platforms/FPSOs sorted by `latest_oil_bpd` desc (`source`, `facility_id`,"
            " `facility_name`, `facility_type`, `fields`, `basin`, `operator`, `status`,"
            " `start_year`, `water_depth_m`, `oil_capacity_bpd`, `gas_capacity_e3m3d`,"
            " `first_year`, `last_year`, `latest_period`, `latest_oil_bpd`, `latest_gas_e3m3d`,"
            " `oil_utilization`, `gas_utilization`, `lon`, `lat`).",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/basin_monthly.json",
            "rollups/basin_monthly.json",
            "Monthly rate series keyed by `{source: {basin: [{p, ...}]}}`.",
            base=base,
            sizes=sizes,
        ),
    ]


def _detailed_json_section(
    sources_list: list[dict[str, Any]],
    *,
    base: str,
    sizes: Mapping[str, int],
) -> list[str]:
    lines = [
        "## Detailed JSON Shards & Search Index (May Exceed Web-Fetch Limits)",
        "",
        "Large JSON shards can exceed plain HTTP fetch truncation limits; prefer the Parquet"
        " rollups below with DuckDB or Python when available.",
        "",
    ]
    source_ids = [str(s["id"]) for s in sources_list]
    for src in source_ids:
        field_rel = f"rollups/field_monthly/{src}.json"
        if not sizes or field_rel in sizes:
            lines.append(
                _endpoint_bullet(
                    field_rel,
                    field_rel,
                    f"Monthly rates for `{src}` fields keyed by `{{field_id: [{{p, ...}}]}}`.",
                    base=base,
                    sizes=sizes,
                )
            )
    for src in source_ids:
        fac_rel = f"rollups/facility_monthly/{src}.json"
        if fac_rel in sizes:
            lines.append(
                _endpoint_bullet(
                    fac_rel,
                    fac_rel,
                    f"Monthly rates and `oil_util` for `{src}` facilities keyed by"
                    " `{facility_id: [{p, ..., oil_util}]}`.",
                    base=base,
                    sizes=sizes,
                )
            )
    lines.append(
        _endpoint_bullet(
            "search/index.json",
            "search/index.json",
            'Compact array of fields, facilities, and wells. Keys: `t` (`"field"|"facility"|'
            '"well"`), `s` (source), `id`, `n` (name), `f` (field/fields), `b` (basin/region),'
            " `lon`, `lat`, `fy` (first year), `ly` (last year), `lo` (latest oil `bbl/d`),"
            " `cap` (oil capacity `bbl/d`).",
            base=base,
            sizes=sizes,
        )
    )
    return lines


def _parquet_and_mirror_section(
    meta: Mapping[str, Any],
    *,
    base: str,
    sizes: Mapping[str, int],
) -> list[str]:
    mirror_info = meta.get("mirror") or {}
    mirror_repo = str(mirror_info.get("repo", "mralbu/ogprod"))
    mirror_rev = str(mirror_info.get("revision", "main"))
    mirror_base = str(mirror_info.get("base_url", "")).rstrip("/")
    rate_cols = ", ".join(f"`{c}`" for c in schema.RATE_COLUMNS)
    prod_cols = ", ".join(f"`{c}`" for c in schema.PRODUCTION_COLUMNS)

    lines = [
        "## Parquet Rollups & Full Hugging Face Mirror (DuckDB / Python)",
        "",
        "Pre-aggregated Parquet rollups (columnar, HTTP range-request friendly):",
        "",
        _endpoint_bullet(
            "rollups/country_monthly.parquet",
            "rollups/country_monthly.parquet",
            f"Columns: `source`, `period`, {rate_cols}.",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/basin_monthly.parquet",
            "rollups/basin_monthly.parquet",
            f"Columns: `source`, `basin`, `period`, {rate_cols}.",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/field_monthly.parquet",
            "rollups/field_monthly.parquet",
            f"Columns: `source`, `field_id`, `field_name`, `period`, {rate_cols}.",
            base=base,
            sizes=sizes,
        ),
        _endpoint_bullet(
            "rollups/facility_monthly.parquet",
            "rollups/facility_monthly.parquet",
            f"Columns: `source`, `facility_id`, `facility_name`, `period`, {rate_cols},"
            " `oil_capacity_bpd`, `gas_capacity_e3m3d`, `oil_utilization`.",
            base=base,
            sizes=sizes,
        ),
        "",
        f"Full Layer-1 canonical store on Hugging Face (`{mirror_repo}@{mirror_rev}`):",
        "",
        f"- [manifest.json]({mirror_base}/manifest.json): SHA-256 checksums, byte sizes,"
        " row counts, and available partitions.",
        f"- Production partitions: `{mirror_base}/production/source=<source>/year=<yyyy>/"
        f"data.parquet` — Columns: {prod_cols}.",
        f"- GeoParquet 1.1 (`EPSG:4326` + `bbox` covering):"
        f" `{mirror_base}/geo/fields/<source>.parquet`,"
        f" `{mirror_base}/geo/facilities/<source>.parquet`,"
        f" `{mirror_base}/geo/wells/<source>.parquet`.",
        "- **Well-level pruning tip:** read `fy`..`ly` for a well from `search/index.json`"
        " first and pass only those `year=<yyyy>` partition URLs to `read_parquet([...])`.",
    ]
    return lines


def _pick_sample_partition(sources_list: list[dict[str, Any]]) -> tuple[str, int]:
    for src in sources_list:
        years = src.get("years") or []
        if src.get("entity_type") == "well" and years:
            return str(src["id"]), int(years[-1])
    for src in sources_list:
        years = src.get("years") or []
        if years:
            return str(src["id"]), int(years[-1])
    return "brazil", 2026


def _query_examples_section(meta: Mapping[str, Any], *, base: str) -> list[str]:
    sources_list = list(meta.get("sources") or [])
    mirror_base = str((meta.get("mirror") or {}).get("base_url", "")).rstrip("/")
    field_pq_url = _data_href(base, "rollups/field_monthly.parquet")
    sample_src, sample_year = _pick_sample_partition(sources_list)
    sample_part_url = (
        f"{mirror_base}/production/source={sample_src}/year={sample_year}/data.parquet"
    )

    return [
        "## Query Examples",
        "",
        "DuckDB over the pre-aggregated field rollup (top oil fields in the latest month):",
        "",
        "```sql",
        "WITH fields AS (",
        "  SELECT source, field_name, period, oil_prod, gas_prod",
        f"  FROM read_parquet('{field_pq_url}')",
        ")",
        "SELECT source, field_name, period,",
        "       round(oil_prod, 0) AS oil_bpd,",
        "       round(gas_prod, 1) AS gas_e3m3d",
        "FROM fields",
        "WHERE period = (SELECT max(period) FROM fields)",
        "ORDER BY oil_prod DESC",
        "LIMIT 10;",
        "```",
        "",
        f"DuckDB over a single Hugging Face Layer-1 partition (`{sample_src}`, {sample_year}):",
        "",
        "```sql",
        "SELECT entity_id, field_name, facility_name, period, oil_prod, gas_prod",
        f"FROM read_parquet('{sample_part_url}')",
        "ORDER BY oil_prod DESC NULLS LAST",
        "LIMIT 10;",
        "```",
        "",
        "CLI (`ogprod`, install with `uv tool install ogprod` or run via `uvx ogprod`):",
        "",
        "```sh",
        f"ogprod ingest {sample_src} --year {sample_year} --data-dir ./data",
        'ogprod sql "SELECT field_name, round(sum(oil_prod), 0) AS oil_bpd'
        ' FROM production GROUP BY 1 ORDER BY 2 DESC LIMIT 10" --data-dir ./data',
        "ogprod skill",
        "```",
    ]


def _deep_links_section(
    sources_list: list[dict[str, Any]],
    *,
    base: str,
    sample_field: tuple[str, str] | None,
) -> list[str]:
    source_slugs = (
        ", ".join(f"`{s['id']}`" for s in sources_list)
        or "`argentina`, `brazil`, `mexico`, `netherlands`, `norway`, `uk`"
    )
    sample_src, sample_fid = sample_field or ("brazil", "BUZIOS")
    encoded_src = quote(sample_src, safe="")
    encoded_fid = quote(sample_fid, safe="")
    example_url = (
        f"{base}?source={encoded_src}&entity=field:{encoded_src}/{encoded_fid}"
        "&products=oil_prod,gas_prod"
    )
    return [
        "## Deep Links into the Web Explorer",
        "",
        "Link users directly to a filtered chart and map view using these query parameters:",
        "",
        f"- `source`: source slug ({source_slugs}; omit for all sources)",
        "- `entity`: `field:<source>/<field_id>`, `facility:<source>/<facility_id>`,"
        " `well:<source>/<well_id>`, or `basin:<source>/<basin>`",
        f"- `products`: comma-separated subset of `{_EXPLORER_PRODUCTS_CSV}`",
        "- `units`: `oilfield_e3m3` (default), `oilfield_mmcf`, or `metric_m3`",
        "",
        f"Example: {example_url}",
    ]


def render_llms_txt(
    meta: Mapping[str, Any],
    *,
    asset_sizes: Mapping[str, int] | None = None,
    site_url: str | None = None,
    sample_field: tuple[str, str] | None = None,
) -> str:
    """Render the Explorer ``llms.txt`` guide from exported metadata and file sizes."""
    base = normalize_site_url(site_url)
    sizes = asset_sizes or {}
    sources_list = list(meta.get("sources") or [])
    generated_at = str(meta.get("generated_at", ""))
    countries = ", ".join(str(s.get("country", s["id"])) for s in sources_list)
    coverage_blurb = f" covering {countries}" if countries else ""
    built_blurb = f" Generated {generated_at}." if generated_at else ""

    sections: list[list[str]] = [
        [
            "# ogprod — Public Oil & Gas Production Explorer",
            "",
            f"> Static public oil, gas, condensate, NGL, water production & injection dataset"
            f" and geospatial explorer{coverage_blurb}.{built_blurb}",
            "",
            "The Web Explorer is a client-side SPA. To explore the data directly without a"
            " browser:",
            "",
            "- **Web-fetch / chat LLMs:** start with the lightweight JSON endpoints below"
            " (`meta.json`, `country_monthly.json`, `fields_summary.json`,"
            " `facilities_summary.json`).",
            "- **Code / DuckDB / CLI agents:** query the Parquet rollups or the full Layer-1"
            " Hugging Face mirror directly with DuckDB HTTPFS (`read_parquet('https://...')`),"
            " or use the `ogprod` CLI (`ogprod skill` prints CLI usage).",
        ],
        _coverage_section(sources_list),
        _units_section(sources_list),
        _lightweight_json_section(base=base, sizes=sizes),
        _detailed_json_section(sources_list, base=base, sizes=sizes),
        _parquet_and_mirror_section(meta, base=base, sizes=sizes),
        _query_examples_section(meta, base=base),
        _deep_links_section(sources_list, base=base, sample_field=sample_field),
    ]
    return "\n\n".join("\n".join(sec) for sec in sections) + "\n"
