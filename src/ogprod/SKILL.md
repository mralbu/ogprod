---
name: ogprod
description: >-
  Public oil & gas production data pipeline and geospatial store. Use when running the `ogprod` command or answering
  questions about it.
---

# ogprod

Public oil & gas production data pipeline and geospatial store.

Install with `uv tool install ogprod` (or, inside the project, run it as
`uv run ogprod`). `ogprod --help` and `ogprod COMMAND --help`
list every option.

## Commands

| Command | Description |
|---|---|
| `sources` | List registered data source connectors |
| `ingest SOURCE [OPTIONS]` | Sync pre-built Parquet from the Hugging Face mirror (`--origin mirror`, default) or fetch live from regulator portals (`--origin publisher`). Supports repeatable `--year`, `--mirror`, `--revision`, `--environment`, `--recent`, `--refresh`, `--no-geo`, `--keep-raw`, `--files`, `--data-dir` |
| `coverage [--data-dir DIR]` | Summarize stored production rows, periods, and geo counts per source |
| `audit [OPTIONS]` | Audit stored Parquet and GeoParquet tables for continuity, zero-hydrocarbon months, staleness, join coverage, and geo hygiene. Supports `--source`, `--stale-months`, `--strict`, `--json`, `--data-dir` |
| `sql QUERY [--data-dir DIR]` | Run a DuckDB SQL query over `production`, `fields`, `facilities`, and `wells` |
| `export-web [OPTIONS]` | Export Layer-2 static web visualization artifacts (`meta.json`, `llms.txt`, `rollups/`, `search/index.json`, `tiles/ogprod.pmtiles`). Supports `--data-dir`, `--out-dir`, `--sync-mirror`, `--mirror`, `--revision`, `--skip-tiles`, `--site-url` |
| `completion SHELL` | Print the completion script for bash, zsh or fish |
| `skill [--install]` | Print this file, or install it as an agent skill |

## Units

All rates in `production` are calendar-day averages stored as `float32`:
- `oil_prod`, `condensate_prod`, `ngl_prod`, `water_prod`, `water_inj`: `bbl/d`
- `gas_prod`, `gas_inj`, `co2_inj`: `e3m3/d` (`10³ m³/d`, thousand m³/day)

## Examples

```sh
ogprod sources
ogprod ingest brazil --year 2025 --year 2026 --data-dir ./data
ogprod ingest norway --data-dir ./data
ogprod ingest mexico --origin publisher --data-dir ./data
ogprod ingest uk --year 2025 --year 2026 --origin publisher --data-dir ./data
ogprod ingest argentina --year 2025 --year 2026 --origin publisher --data-dir ./data
ogprod ingest netherlands --year 2025 --year 2026 --origin publisher --data-dir ./data
ogprod coverage --data-dir ./data
ogprod audit --data-dir ./data
ogprod export-web --data-dir ./data --out-dir ./site/public/data
ogprod sql "SELECT source, field_name, round(sum(oil_prod), 0) AS oil_bpd FROM production WHERE period = '2025-01-01' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10" --data-dir ./data
```
