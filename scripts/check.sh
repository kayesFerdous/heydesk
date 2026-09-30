#!/usr/bin/env bash
# Everything CI runs. Run before every commit.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
scripts/check_imports.sh
