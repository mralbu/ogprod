# ogprod

Public oil & gas production data pipeline and geospatial store.

## Commands

- `just check`: lint (ruff, ty) and tests; run it before finishing a change.
- `just dead-code` / `just complexity`: unused code (vulture) / complexity report (radon).
- `just format`: format and auto-fix.
- `just test -k NAME`: run selected tests.
- `just run ARGS`: run the CLI.

Use `uv add PKG` / `uv add --dev PKG` to add dependencies; never edit `uv.lock` by hand.

## Conventions

- Source in `src/ogprod/`, tests in `tests/`, Python 3.14+.
- Keep logic in pure functions (`core.py` and friends); `cli.py` only parses
  arguments and prints.
- ruff enforces bandit's security rules and a McCabe complexity of at most 10 per function:
  split a function that exceeds it instead of adding `noqa` or raising the limit.
- Use `assert` only for invariants and type narrowing, never to validate input or
  permissions (`python -O` strips it): raise an exception for those.
- Type-annotate public functions; `ty check` must pass with no warnings.
- Every behavior change comes with a test. Where a property holds for all inputs
  (round-trips, invariants, agreement with a simpler implementation), test it with
  Hypothesis (`@given`) rather than a few hand-picked examples.
- Semantic versioning: the public API is what `__init__.py` exports plus the CLI.
  Never edit the version in `pyproject.toml` (releases bump it with `just release`); say so
  when a change breaks the public API (`just api-check` lists breaking changes).
- When commands or options change, update `src/ogprod/SKILL.md` too (it is
  what `ogprod skill` prints).
- Avoid tautological comments that restate the code. Use brief inline comments only for
  non-obvious invariants, units, or external quirks, and record cross-cutting decisions
  in `docs/adr/`.
