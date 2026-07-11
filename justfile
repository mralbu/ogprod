# List available recipes
default:
    @just --list

# Install/sync all dependencies (including dev tools)
sync:
    uv sync

# Download a source's raw files into data/ (`just fetch sodir`, `just fetch anp` = whole archive, `just fetch anp --year 2023 --month 1`)
fetch *args:
    uv run python -m ogprod fetch {{args}}

# Re-execute the example notebooks in place (downloads data on first run)
notebooks:
    uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb

# Run the test suite
test *args:
    uv run pytest {{args}}

# Lint with ruff
lint:
    uv run ruff check src tests
    uv run ruff format --check src tests

# Auto-format and fix lint issues
fmt:
    uv run ruff format src tests
    uv run ruff check --fix src tests

# Type-check with ty
typecheck:
    uv run ty check

# Run everything CI would: lint, types, tests
check: lint typecheck test
