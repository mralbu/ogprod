# 0007. Connectors register themselves on import

Status: accepted

## Context

The CLI and store look connectors up by name (`base.get`, `base.available`).

## Decision

Each connector class is decorated with `@base.register`, and `ogprod/sources/__init__.py`
imports every connector module so importing `ogprod.sources` fills the registry.

## Consequences

A new connector must be imported in `ogprod/sources/__init__.py`, even though nothing
else uses the import, or it is not available.
