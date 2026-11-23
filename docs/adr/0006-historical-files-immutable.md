# 0006. Historical source files are immutable unless refreshed

Status: accepted

## Context

Re-downloading the full archive on every ingest is slow and hard on publishers, and
agencies rarely restate data more than a year old.

## Decision

`store._should_skip_remote` skips a remote file already recorded in the manifest when its
`year_max` is older than the previous calendar year. `refresh=True` (`--refresh`) forces a
re-download.

## Consequences

Restatements of old years are only picked up with `--refresh`.
