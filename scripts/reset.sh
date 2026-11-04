#!/usr/bin/env bash
# A clean demo: a fresh database and a demo login. The dev agent is made on the
# server's next start, from DEV_AGENT_SEED.
# Deletes the default SQLite file, heydesk.db, and everything in it.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -f heydesk.db
uv run alembic upgrade head
uv run heydesk create-user demo@heydesk.test "demo password"
uv run heydesk create-admin admin@heydesk.test "admin password"
echo
echo "Now start the server: uv run uvicorn heydesk.main:app"
