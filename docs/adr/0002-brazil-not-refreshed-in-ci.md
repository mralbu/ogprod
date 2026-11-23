# 0002. Brazil is not refreshed on GitHub Actions

Status: accepted

## Context

`www.gov.br` resets TCP connections from GitHub Actions' Azure IP ranges, so ANP downloads
fail on hosted runners regardless of retries.

## Decision

`scripts/ci_refresh.py` refreshes only `DEFAULT_CI_SOURCES = ("norway", "mexico")`. Brazil
is seeded and updated from a machine that can reach gov.br, with
`scripts/publish_mirror.py`, or by triggering the workflow manually when the portal is
reachable.

## Consequences

The Brazil partitions on the mirror lag until someone refreshes them locally. Revisit if
gov.br stops blocking Azure or a self-hosted runner becomes available.
