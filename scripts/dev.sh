#!/usr/bin/env bash
# One command for development: the backend (:8000) and the frontend (:8080).
#   scripts/dev.sh            both, for you
#   scripts/dev.sh --tunnel   both, plus ONE public link for everyone (Cloudflare Tunnel)
# The public link is the frontend. The voice and chat page is at <link>/demo/
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT  # Ctrl+C stops everything this script started

uv run alembic upgrade head
uv run uvicorn heydesk.main:app --reload &
(cd ../heydesk-frontend && pnpm run serve) &

if [[ "${1:-}" == "--tunnel" ]]; then
  sleep 8  # let the frontend come up first
  cloudflared tunnel --url http://localhost:8080 &
fi
wait
