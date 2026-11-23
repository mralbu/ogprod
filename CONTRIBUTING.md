# Development

Requires [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

Run `just` to list all grouped recipes (or `just --list --group GROUP` to filter):

```sh
# Setup
just sync                # create/update .venv
just pre-commit-install  # install the git hooks (pre-commit and pre-push)

# Quality
just check               # lint + test (what CI runs)
just format              # ruff format + ruff check --fix
just lint                # ruff (incl. security and complexity rules) + ty
just pre-commit          # prek hooks on all files

# Test
just test                # pytest
just cov                 # pytest under coverage, with a terminal report
just cov-report          # HTML report of the last cov run in htmlcov/

# Analysis
just dead-code           # unused code (vulture)
just complexity          # complexity and maintainability per function/module (radon)

# CLI
just run ...             # run the CLI, e.g. just run --help
just skill-install       # install SKILL.md into ~/.claude/skills

# Release
just build               # wheel and sdist in dist/
just smoke               # install the built wheel in a clean environment and verify it
just api-check           # public API changes since the last release (griffe)
just release BUMP        # bump the version, check, commit and tag

# Maintenance
just upgrade             # upgrade the locked dependencies
just clean               # remove caches and dist/
```

The hooks ([prek](https://prek.j178.dev), `.pre-commit-config.yaml`) check whitespace,
end of file, YAML/TOML, merge conflicts, large files and private keys, plus
`uv lock --check`, `ruff format`, `ruff check` and `ty` on every commit; `pytest` runs on push.

## Code quality

`just lint` (and the hooks) run [ruff](https://docs.astral.sh/ruff/) with, besides style
and bug rules, the security checks of bandit (`S`) and McCabe complexity (`C90`): a
function with a cyclomatic complexity above 10 fails; split it rather than raising the
limit. `assert` is allowed (bandit's `S101` is off), but only for invariants and type
narrowing: `python -O` strips it, so validate input, permissions or anything else that must
always hold with `raise`. [ty](https://docs.astral.sh/ty/)
checks the types.

Two reports, not part of `just check`, help when cleaning up:

- `just dead-code`: [vulture](https://github.com/jendrikseipp/vulture) lists functions,
  classes and variables that nothing uses. It guesses, so check each hit; a public function
  only users call shows up too. Silence false positives in `[tool.vulture]`.
- `just complexity`: [radon](https://radon.readthedocs.io) grades each function's
  cyclomatic complexity (A best, F worst) and each module's maintainability index.

With [direnv](https://direnv.net/), `direnv allow` activates `.venv` when you enter the
directory and installs the fish completions into `~/.config/fish/completions`.

## Layout

| Path | Contents |
|---|---|
| `src/ogprod/core.py` | Core logic (pure functions, no I/O) |
| `src/ogprod/cli.py` | Commands (click), including `completion` and `skill` |
| `src/ogprod/SKILL.md` | Agent skill printed by `ogprod skill`; keep it in sync with the CLI |
| `tests/` | pytest tests, with Hypothesis for property-based tests |

## The agent skill

`SKILL.md` ships inside the package, so the text an agent reads always matches the
installed version of the CLI. Update it whenever commands or options change; the tests
check that its frontmatter `name` matches the command name.

## Versioning and releases

Versions follow [semantic versioning](https://semver.org/) in
[PEP 440](https://peps.python.org/pep-0440/) form: a breaking change to the public API
bumps the major version (the minor one before 1.0), a new feature the minor version and
a fix the patch version. The public API is what `src/ogprod/__init__.py`
exports, plus the commands and options of the CLI.

The version lives only in `pyproject.toml`; `ogprod.__version__` reads it from
the installed package metadata. Change it with `uv version` (never by hand), which also
updates `uv.lock`. To release, on an up-to-date `main`:

```sh
just release minor       # or major, patch; "patch beta" for 1.2.4b1, "stable" to drop it
git push --follow-tags
```

`just release` refuses a dirty tree or an existing tag, runs `just check`, and runs
`just api-check` against the new version: [griffe](https://mkdocstrings.github.io/griffe/)
compares the public API with the last `v*` tag, and breaking changes fail the release
unless the bump is major (minor before 1.0). Then it commits `chore(release): vX.Y.Z` and
tags `vX.Y.Z`. `just release` without a bump tags the current version (the first release).
griffe only sees the Python API: decide CLI breaking changes yourself.

CI reports breaking changes on pull requests as annotations without failing. Pushing a
`v*` tag runs `.github/workflows/release.yml`: it checks that the tag matches the
version, runs the checks and `api-check` again, builds and smoke-tests the wheel, and
creates a GitHub release with the wheel and sdist.

## Updating from the template

This project was generated with [Copier](https://copier.readthedocs.io/). To pull in
template changes:

```sh
uvx copier update
```
