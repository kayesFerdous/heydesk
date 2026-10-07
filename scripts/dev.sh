#!/usr/bin/env bash
# One command for development: the backend (:8000) and the frontend (:8080).
#   scripts/dev.sh            both; with PUBLIC_URL in .env, the frontend is also on that ngrok
#                             URL, else only on http://localhost:8080
#   scripts/dev.sh --tunnel   no PUBLIC_URL: a throwaway Cloudflare link instead
# The frontend's dev server passes /api and /demo (the chat and voice page) to the backend, so
# the one link serves both, and Telegram's webhook (PUBLIC_URL/api/telegram/…) reaches it too.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT  # Ctrl+C stops everything this script started

FRONTEND=../../vue/heydesk-frontend
PUBLIC_URL=$(uv run python -c "from heydesk.config import Settings; print(Settings().PUBLIC_URL)")

uv run alembic upgrade head
uv run uvicorn heydesk.main:app --reload &
(cd "$FRONTEND" && pnpm run serve) &

if [[ -n "$PUBLIC_URL" ]]; then
  sleep 8  # let the frontend come up first
  ngrok http 8080 --url "$PUBLIC_URL" --log stdout --log-level warn &
  echo "Frontend: $PUBLIC_URL (and http://localhost:8080)"
elif [[ "${1:-}" == "--tunnel" ]]; then
  sleep 8
  cloudflared tunnel --url http://localhost:8080 &
else
  echo "Frontend: http://localhost:8080 (set PUBLIC_URL in .env for a public link)"
fi
wait
