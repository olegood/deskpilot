#!/usr/bin/env bash
# Runs every check that must pass before a commit.
# Usage from the repo root:
#   ./scripts/check.sh            # fast checks only (no external services)
#   ./scripts/check.sh --all      # also the integration tests (needs Ollama + Postgres)
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"

run() {
    echo "── $* ──"
    "$@"
}

cd "$root/backend"
run uv sync --locked
run uv run ruff check .
run uv run ruff format --check .
run uv run mypy src
run uv run pytest

if [[ -d "$root/frontend/node_modules" ]]; then
    cd "$root/frontend"
    # types first: the generated schema is an input to the type check, and a stale
    # one hides exactly the drift it exists to catch.
    run pnpm types
    run pnpm lint
    run pnpm exec tsc --noEmit
    run pnpm test
else
    echo "── skipping frontend: run pnpm install in frontend/ ──"
fi

# The fake vendors are separate projects with their own dependencies and their own
# test suites. They are checked here so a change to one cannot be forgotten.
for vendor in "$root"/vendors/*/; do
    [[ -f "$vendor/pyproject.toml" ]] || continue
    echo "── $(basename "$vendor") ──"
    cd "$vendor"
    run uv sync --locked
    run uv run ruff check .
    run uv run ruff format --check .
    run uv run mypy src
    run uv run pytest
done

if [[ "${1:-}" == "--all" ]]; then
    cd "$root/backend"
    run uv run pytest -m integration
fi

echo "All checks passed."
