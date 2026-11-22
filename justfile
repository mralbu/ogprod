set shell := ["bash", "-euo", "pipefail", "-c"]

# List recipes
default:
    @just --list

# Create/update .venv with the dev dependencies
sync:
    uv sync

# Format code and apply ruff's safe fixes
format:
    uv run ruff format
    uv run ruff check --fix

# Lint: ruff (style, errors, security, complexity) and ty (types)
lint:
    uv run ruff format --check
    uv run ruff check
    uv run ty check

# Report unused code (vulture, configured in pyproject.toml). Silence a false positive with
# ignore_names / ignore_decorators in [tool.vulture], or a whitelist file passed as an argument
dead-code *args:
    uv run vulture {{ args }}

# Report complexity per function (radon cc, F worst) and maintainability per module (radon mi)
complexity *args="src":
    uv run radon cc --show-complexity --average --order SCORE {{ args }}
    uv run radon mi --show {{ args }}

# Run the tests, e.g. just test -k cli
test *args:
    uv run pytest {{ args }}

# Run the tests under coverage and print the report, e.g. just cov -k cli
cov *args:
    uv run coverage run -m pytest {{ args }}
    uv run coverage report

# Write an HTML report of the last coverage run to htmlcov/
cov-report:
    uv run coverage html

# Lint + tests (what CI runs)
check: lint test

# Run the prek hooks on all files (or: just pre-commit --files X)
pre-commit *args="--all-files":
    uv run prek run {{ args }}

# Install the prek git hooks (pre-commit and pre-push)
pre-commit-install:
    uv run prek install

# Run the CLI, e.g. just run --help
run *args:
    @uv run ogprod {{ args }}

# Install the agent skill (SKILL.md) into ~/.claude/skills, or: just skill-install .claude/skills
skill-install dir="":
    @uv run ogprod skill --install {{ if dir != "" { "--skills-dir " + quote(dir) } else { "" } }}

# Upgrade the locked dependencies
upgrade:
    uv lock --upgrade
    uv sync

# Build wheel and sdist into dist/ (ignoring [tool.uv.sources], as when publishing)
build:
    rm -rf dist
    uv build --no-sources

# Install the built wheel into a clean environment and import it (after just build)
smoke:
    uv run --isolated --no-project --with dist/*.whl python -c "import ogprod; print(ogprod.__version__)"
    uv run --isolated --no-project --with dist/*.whl ogprod --version

# Check the public API against the last release tag with griffe: a breaking change needs a
# major bump (a minor one before 1.0). VERSION defaults to the current one.
api-check version="":
    #!/usr/bin/env bash
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

# Bump the version, check, commit and tag, e.g. just release minor; just release (as is)
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

# Remove caches and build artifacts (keeps .venv)
clean:
    rm -rf dist build .pytest_cache .ruff_cache .coverage htmlcov
    find . -path ./.venv -prune -o -type d -name __pycache__ -exec rm -rf {} +
