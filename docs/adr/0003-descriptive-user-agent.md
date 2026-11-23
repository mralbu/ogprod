# 0003. Send a descriptive User-Agent

Status: accepted

## Context

Portals such as gov.br answer 403 to the HTTP client's default User-Agent.

## Decision

Every request sends `base.USER_AGENT` (`ogprod/0.1 (+repository URL)`), which also
identifies the project to data publishers.

## Consequences

`USER_AGENT` hardcodes `0.1`; deriving it from `ogprod.__version__` would keep it current.
