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
| `ingest SOURCE [OPTIONS]` | Download, normalize, and store production & geospatial data (`--year`, `--environment`, `--recent`, `--refresh`, `--no-geo`, `--keep-raw`, `--files`, `--data-dir`) |
| `coverage [--data-dir DIR]` | Summarize stored production rows, periods, and geo counts per source |
| `sql QUERY [--data-dir DIR]` | Run a DuckDB SQL query over `production`, `fields`, `facilities`, and `wells` |
| `completion SHELL` | Print the completion script for bash, zsh or fish |
| `skill [--install]` | Print this file, or install it as an agent skill |

## Units

All rates in `production` are calendar-day averages stored as `float32`:
- `oil_prod`, `condensate_prod`, `ngl_prod`, `water_prod`, `water_inj`: `bbl/d`
- `gas_prod`, `gas_inj`, `co2_inj`: `e3m3/d` (`10³ m³/d`, thousand m³/day)

## Examples

```sh
ogprod sources
ogprod ingest anp --year 2026 --data-dir ./data
ogprod ingest sodir --data-dir ./data
ogprod ingest mexico --data-dir ./data
ogprod coverage --data-dir ./data
ogprod sql "SELECT source, field_name, round(sum(oil_prod), 0) AS oil_bpd FROM production WHERE period = '2025-01-01' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10" --data-dir ./data
```
