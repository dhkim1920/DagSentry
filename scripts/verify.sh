#!/usr/bin/env bash
set -euo pipefail

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/dagsentry-uv-cache}"
export DAGSENTRY_DATABASE_URL="${DAGSENTRY_DATABASE_URL:-postgresql+psycopg://dagsentry_demo:dagsentry_demo@localhost:5432/dagsentry_demo}"
export DAGSENTRY_TEST_DATABASE_URL="${DAGSENTRY_TEST_DATABASE_URL:-$DAGSENTRY_DATABASE_URL}"

docker compose -f compose.yaml -f compose.demo.yaml up -d --wait postgres
uv sync --locked --extra airflow
uv run alembic upgrade head
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
