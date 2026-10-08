#!/usr/bin/env bash
# One command for development: the backend (:8000) and the frontend (:8080).
#   scripts/dev.sh            both; with PUBLIC_URL in .env, the frontend is also on that ngrok
#                             URL, else only on http://localhost:8080
#   scripts/dev.sh --tunnel   a throwaway Cloudflare link instead of ngrok, even with PUBLIC_URL
# The frontend's dev server passes /api and /demo (the chat and voice page) to the backend, so
# the one link serves both, and Telegram's webhook (PUBLIC_URL/api/telegram/…) reaches it too.
set -euo pipefail
cd "$(dirname "$0")/.."
# Ctrl+C stops everything this script started, and waits for each to finish its shutdown lines,
# so the prompt comes back after them, not in the middle
stop() {
  trap - INT TERM EXIT
  kill $(jobs -p) 2>/dev/null
  wait
}
trap stop INT TERM EXIT

FRONTEND=../heydesk-frontend
PUBLIC_URL=$(uv run python -c "from heydesk.config import Settings; print(Settings().PUBLIC_URL)")

uv run alembic upgrade head
uv run uvicorn heydesk.main:app --reload &
# the frontend builds its chat links on PUBLIC_URL, so they work from any phone
(cd "$FRONTEND" && VUE_APP_PUBLIC_URL="$PUBLIC_URL" pnpm run serve) &

if [[ "${1:-}" == "--tunnel" ]]; then
  sleep 8  # let the frontend come up first
  cloudflared tunnel --protocol http2 --url http://localhost:8080 &  # QUIC (UDP) is blocked here
elif [[ -n "$PUBLIC_URL" ]]; then
  sleep 8
  ngrok http 8080 --url "$PUBLIC_URL" --log stdout --log-level warn &
  echo "Frontend: $PUBLIC_URL (and http://localhost:8080)"
else
  echo "Frontend: http://localhost:8080 (set PUBLIC_URL in .env for a public link)"
fi
wait
