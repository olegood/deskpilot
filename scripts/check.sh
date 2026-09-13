#!/usr/bin/env bash
# Runs every check that must pass before a commit.
# Usage from the repo root:
#   ./scripts/check.sh            # fast checks only (no external services)
#   ./scripts/check.sh --all      # also the integration tests (needs Ollama + Postgres)
set -euo pipefail

cd "$(dirname "$0")/../backend"

run() {
    echo "── $* ──"
    "$@"
}

run uv sync --locked
run uv run ruff check .
run uv run ruff format --check .
run uv run mypy src
run uv run pytest

if [[ "${1:-}" == "--all" ]]; then
    run uv run pytest -m integration
fi

echo "All checks passed."