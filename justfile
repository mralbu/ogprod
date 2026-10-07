# List available recipes
default:
    @just --list

# Install/sync all dependencies (including dev tools and notebook deps, which ty needs)
sync:
    uv sync --all-groups

# Download a source's raw files into data/ (`just fetch sodir`, `just fetch anp` = whole archive, `just fetch anp --year 2023 --month 1`)
fetch *args:
    uv run python -m ogprod fetch {{args}}

# Fetch + parse + upsert a source into data/store (`just ingest sodir`, `just ingest anp --year 2023`, `just ingest sodir --entities`)
ingest *args:
    uv run python -m ogprod ingest {{args}}

# Re-execute the example notebooks in place (downloads data on first run)
notebooks:
    uv run --group notebooks jupyter nbconvert --to notebook --execute --inplace notebooks/*.ipynb

# Serve Jupyter notebooks on all interfaces (`just jupyter`, `just jupyter 8889`)
jupyter port="8888":
    uv run --group notebooks jupyter notebook --ip 0.0.0.0 --port {{port}} notebooks/

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
