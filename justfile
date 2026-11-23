# Task runner for ogprod (https://just.systems).
# Run `just` to list recipes, `just --list --group GROUP` to filter, or `just --show RECIPE`.

set shell := ["bash", "-euo", "pipefail", "-c"]

[private]
default:
    @just --list --unsorted

# --- Setup ---

# Create or update .venv with dev dependencies
[group('setup')]
sync:
    uv sync

# Install the prek git hooks (pre-commit and pre-push)
[group('setup')]
pre-commit-install:
    uv run prek install

# --- Quality ---

# Run lint and tests (what CI runs)
[group('quality')]
check: lint test

# Format code and apply ruff's safe auto-fixes
[group('quality')]
format:
    uv run ruff format
    uv run ruff check --fix

# Check formatting, ruff (style, errors, security, complexity), and ty (types)
[group('quality')]
lint:
    uv run ruff format --check
    uv run ruff check
    uv run ty check

# Run prek hooks on all files (e.g. just pre-commit --files X)
[group('quality')]
pre-commit *args="--all-files":
    uv run prek run {{ args }}

# --- Test ---

# Run tests with pytest (e.g. just test -k cli)
[group('test')]
test *args:
    uv run pytest {{ args }}

# Run tests under coverage and print a terminal summary (e.g. just cov -k cli)
[group('test')]
cov *args:
    uv run coverage run -m pytest {{ args }}
    uv run coverage report

# Write an HTML report of the last coverage run to htmlcov/
[group('test')]
cov-report:
    uv run coverage html

# --- Analysis ---

# Report unused code with vulture (configure ignores in [tool.vulture] or pass a whitelist)
[group('analysis')]
dead-code *args:
    uv run vulture {{ args }}

# Report cyclomatic complexity (radon cc) and maintainability index (radon mi)
[group('analysis')]
complexity *args="src":
    uv run radon cc --show-complexity --average --order SCORE {{ args }}
    uv run radon mi --show {{ args }}

# --- CLI ---

# Run the CLI via uv (e.g. just run --help)
[group('cli')]
run *args:
    @uv run ogprod {{ args }}

# Install agent SKILL.md into ~/.claude/skills (or: just skill-install DIR)
[group('cli')]
skill-install dir="":
    @uv run ogprod skill --install {{ if dir != "" { "--skills-dir " + quote(dir) } else { "" } }}

# --- Release ---

# Build wheel and sdist into dist/ (ignoring [tool.uv.sources])
[group('release')]
build:
    rm -rf dist
    uv build --no-sources

# Install the built wheel in an isolated env and verify import and CLI (after just build)
[group('release')]
smoke:
    uv run --isolated --no-project --with dist/*.whl python -c "import ogprod; print(ogprod.__version__)"
    uv run --isolated --no-project --with dist/*.whl ogprod --version

# Check public API breaking changes against the last release tag with griffe
[group('release')]
api-check version="":
    #!/usr/bin/env bash
    # A breaking change requires a major bump (minor before 1.0). VERSION defaults to current.
    set -euo pipefail
    version={{ quote(version) }}
    version=${version:-$(uv version --short)}
    # In the release workflow HEAD carries the tag being released: compare with the one before.
    rev=HEAD
    if [[ -n $(git tag --points-at HEAD --list "v$version") ]]; then rev=HEAD^; fi
    if ! last=$(git describe --tags --abbrev=0 --match 'v[0-9]*' "$rev" 2>/dev/null); then
        echo "No earlier release tag: nothing to check against"
        exit 0
    fi
    IFS=. read -r last_major last_minor _ <<<"${last#v}"
    IFS=. read -r major minor _ <<<"$version"
    format=${GITHUB_ACTIONS:+github}
    if uv run griffe check ogprod --search src --against "$last" --format "${format:-verbose}"; then
        echo "No breaking changes since $last"
    elif (( major > last_major || (major == 0 && last_major == 0 && minor > last_minor) )); then
        echo "Breaking changes since $last: fine for $version"
    else
        echo "Breaking changes since $last need a major version bump (minor before 1.0), not $version" >&2
        exit 1
    fi

# Bump version, check, commit, and tag (e.g. just release minor; omit bump to tag current)
[group('release')]
release *bump:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ -n $(git status --porcelain) ]]; then
        echo "Commit or stash your changes first" >&2
        exit 1
    fi
    version=$(uv version --short --dry-run {{ prepend("--bump ", bump) }})
    if git rev-parse --quiet --verify "refs/tags/v$version" >/dev/null; then
        echo "v$version is already tagged: pass a bump (major, minor, patch, ...)" >&2
        exit 1
    fi
    just check
    just api-check "$version"
    uv version {{ prepend("--bump ", bump) }}
    git add pyproject.toml uv.lock
    git diff --cached --quiet || git commit --quiet --message "chore(release): v$version"
    git tag --annotate "v$version" --message "v$version"
    echo "Tagged v$version. Push it to release: git push --follow-tags"

# --- Maintenance ---

# Upgrade locked dependencies in uv.lock and sync .venv
[group('maintenance')]
upgrade:
    uv lock --upgrade
    uv sync

# Remove caches, coverage files, and build artifacts (keeps .venv)
[group('maintenance')]
clean:
    rm -rf dist build .pytest_cache .ruff_cache .coverage htmlcov
    find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} +
