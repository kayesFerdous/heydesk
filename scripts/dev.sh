#!/usr/bin/env bash
# HeyDesk's development runner. With no arguments it shows a menu; or name what you want:
#
#   scripts/dev.sh                  the menu
#   scripts/dev.sh api      (b)     the backend on :8000
#   scripts/dev.sh web      (f)     the frontend on :8080
#   scripts/dev.sh all      (a)     both
#   scripts/dev.sh public   (p)     both, and a public link
#   scripts/dev.sh again    (.)     whatever you started last time, the same way
#   scripts/dev.sh stop     (s)     stop what runs on :8000 and :8080, and the tunnels
#   scripts/dev.sh status   (st)    what's running, and the public link
#   scripts/dev.sh logs     (l)     follow the last run's logs: logs api | web | tunnel
#   scripts/dev.sh check    (c)     scripts/check.sh: tests, ruff, format, mypy, imports
#   scripts/dev.sh test     (t)     pytest; anything after it goes to pytest (-k photo, -m live)
#   scripts/dev.sh migrate  (m)     alembic upgrade head
#   scripts/dev.sh migration "…"    a new migration from the models
#   scripts/dev.sh reset            a fresh database and the demo login (scripts/reset.sh)
#   scripts/dev.sh admin            the admins: list, add, a new password, delete (a menu)
#     admin list | add EMAIL [PASSWORD] | password EMAIL [PASSWORD] | delete EMAIL
#     without a PASSWORD one is made up; either way it's printed, since it can't be read back
#   scripts/dev.sh doctor   (d)     is everything installed and set up?
#
# Options for api, web, all and public:
#   -t, --tunnel    a public link: ngrok on PUBLIC_URL if .env has one, else Cloudflare
#   --cf            a throwaway Cloudflare link, even with PUBLIC_URL
#   --fake          offline: fake model and embedder (canned lines, nothing is saved)
#   --groq          talk with Groq instead of .env's LLM_PROVIDER
#   --seed NAME     the agent at / from another seed: shop, shop-bn
#   -o, --open      open the browser when it's up
#   --no-reload     don't restart the backend when the code changes
#
#   e.g.  scripts/dev.sh a --fake --seed shop      scripts/dev.sh p -o
#
# Whatever already runs on :8000 or :8080 is stopped first. Ctrl+C stops everything it started.
# Each run's output is also kept in .logs/ (api.log, web.log, tunnel.log).

set -uo pipefail # not -e: the menu and the clean-up handle failures themselves
cd "$(dirname "$0")/.."

FRONTEND=${HEYDESK_FRONTEND:-../heydesk-frontend}
API_PORT=8000 # fixed: the frontend's dev server passes /api on to :8000
WEB_PORT=8080
LOGS=.logs
SELF=scripts/dev.sh

if [[ -t 1 ]]; then
  B=$'\e[1m' D=$'\e[2m' R=$'\e[31m' G=$'\e[32m' Y=$'\e[33m' M=$'\e[35m' C=$'\e[36m' N=$'\e[0m'
else
  B="" D="" R="" G="" Y="" M="" C="" N=""
fi
ok() { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
die() {
  printf '%s✗%s %s\n' "$R" "$N" "$*" >&2
  exit 1
}

# A setting as the backend will see it: the environment first, then .env, then the default
setting() {
  local key=$1 value=${!1:-}
  if [[ -z $value && -f .env ]]; then
    value=$(grep -E "^$key=" .env | tail -1 | cut -d= -f2-)
    value=${value#[\"\']} && value=${value%[\"\']}
  fi
  printf '%s' "${value:-${2:-}}"
}

# ---------------------------------------------------------------- processes

running() { # still alive (a finished child we haven't waited for is a zombie: not alive)
  local state
  state=$(ps -o stat= -p "$1" 2>/dev/null) && [[ $state != Z* ]]
}

tree() { # a process and everything under it, children first
  local child
  for child in $(pgrep -P "$1"); do tree "$child"; done
  echo "$1"
}

kill_all() { # ask them to stop, give them 5 seconds to finish, then make them
  [[ $# -eq 0 ]] && return 0
  local pids="" p alive
  for p in "$@"; do pids="$pids $(tree "$p")"; done
  kill -TERM $pids 2>/dev/null
  for _ in {1..25}; do
    alive=""
    for p in $pids; do running "$p" && alive="$alive $p"; done
    [[ -z $alive ]] && return 0
    sleep 0.2
  done
  kill -KILL $alive 2>/dev/null
}

listeners() { # the pids listening on a port: ss on Linux (lsof often isn't installed), lsof on macOS
  if command -v ss >/dev/null; then
    ss -ltnpH "sport = :$1" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 | sort -u
  else
    lsof -nP -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | sort -u
  fi
}
browse() { # open a link in the browser: xdg-open on Linux, open on macOS
  if command -v xdg-open >/dev/null; then xdg-open "$1" >/dev/null 2>&1 & else open "$1"; fi
}
copy() { # put stdin on the clipboard; fails when there's no clipboard tool
  if command -v pbcopy >/dev/null; then pbcopy
  elif [[ -n ${WAYLAND_DISPLAY:-} ]] && command -v wl-copy >/dev/null; then wl-copy
  elif command -v xclip >/dev/null; then xclip -selection clipboard
  else return 1; fi
}
describe() { ps -o command= -p "$1" 2>/dev/null | sed -E 's#(^| )/[^ ]*/#\1#g' | cut -c1-60; }

free_port() { # stop whatever listens on the port
  local pids p
  pids=$(listeners "$1")
  [[ -z $pids ]] && return 0
  for p in $pids; do warn ":$1 was taken by $(describe "$p") (pid $p), stopping it"; done
  kill_all $pids
  [[ -z $(listeners "$1") ]] || die "Couldn't free :$1"
}

tunnel_pids() { pgrep -f "ngrok http ($API_PORT|$WEB_PORT)|cloudflared tunnel .*localhost:($API_PORT|$WEB_PORT)"; }
ngrok_url() { curl -s --max-time 1 http://127.0.0.1:4040/api/tunnels | grep -o '"public_url":"[^"]*"' | head -1 | cut -d'"' -f4; }
cloudflare_url() { grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOGS/tunnel.log" 2>/dev/null | head -1; }

tunnel_url() { # the running tunnel's public link, if there is one
  if pgrep -f "ngrok http" >/dev/null; then ngrok_url; else cloudflare_url; fi
}

# ---------------------------------------------------------------- starting

PIDS="" # what this run started, for Ctrl+C
LINK="" # the public link, when there is one
LAST_PID=""

label() { awk -v p="$2$(printf '%-4s' "$1")│$N " '{ print p $0; fflush() }'; }

run() { # NAME COLOR COMMAND…: in the background, its output on screen with a label and in .logs/
  local name=$1 color=$2
  shift 2
  if [[ -n $color ]]; then
    "$@" > >(tee "$LOGS/$name.log" | label "$name" "$color") 2>&1 &
  else # only to the log
    "$@" >"$LOGS/$name.log" 2>&1 &
  fi
  PIDS="$PIDS $!"
  LAST_PID=$!
}

cleanup() {
  trap - INT TERM EXIT
  [[ -z $PIDS ]] && return
  printf '\n%sStopping…%s\n' "$D" "$N"
  kill_all $PIDS
  wait 2>/dev/null
  ok "Stopped"
}

wait_for() { # NAME URL PID: until the URL answers, for up to a minute
  for _ in {1..120}; do
    curl -s -o /dev/null --max-time 1 "$2" && return 0
    running "$3" || die "The $1 stopped while starting (its output is above, and in $LOGS/$1.log)"
    sleep 0.5
  done
  die "The $1 didn't answer on $2 within a minute"
}

start_tunnel() { # KIND PORT: sets LINK
  local kind=$1 port=$2
  LINK=""
  printf '%sOpening a public link (%s)…%s\n' "$D" "$kind" "$N"
  if [[ $kind == ngrok ]]; then
    run tunnel "" ngrok http "$port" --url "$PUBLIC" --log stdout --log-level warn
  else # http2: QUIC (UDP) is blocked on some networks
    run tunnel "" cloudflared tunnel --protocol http2 --url "http://localhost:$port"
  fi
  for _ in {1..60}; do
    if [[ $kind == ngrok ]]; then LINK=$(ngrok_url); else LINK=$(cloudflare_url); fi
    [[ -n $LINK ]] && return 0
    running "$LAST_PID" || break
    sleep 0.5
  done
  tail -n 8 "$LOGS/tunnel.log"
  [[ $kind == ngrok ]] && warn "Is ngrok already running somewhere else? A free account allows one at a time."
  die "The $kind link didn't open"
}

start() { # api | web | all, then options
  local what=$1 tunnel="" open="" reload="--reload" api="" web=""
  shift
  local args="$*"
  while [[ $# -gt 0 ]]; do
    case $1 in
      -t | --tunnel) tunnel=auto ;;
      --cf | --cloudflare) tunnel=cloudflare ;;
      --ngrok) tunnel=ngrok ;;
      --fake) export LLM_PROVIDER=fake EMBEDDER=fake ;;
      --groq) export LLM_PROVIDER=groq ;;
      --seed)
        [[ -f examples/agents/${2:-}.yaml ]] || die "--seed takes one of: $(seeds)"
        export DEV_AGENT_SEED=examples/agents/$2.yaml
        shift
        ;;
      -o | --open) open=1 ;;
      --no-reload) reload="" ;;
      *) die "Unknown option: $1 (see $SELF help)" ;;
    esac
    shift
  done
  case $what in
    api) api=1 ;;
    web) web=1 ;;
    all) api=1 web=1 ;;
  esac

  PUBLIC=$(setting PUBLIC_URL)
  if [[ $tunnel == auto ]]; then
    if [[ -n $PUBLIC ]]; then tunnel=ngrok; else tunnel=cloudflare; fi
  fi
  [[ $tunnel == ngrok && -z $PUBLIC ]] && die "ngrok needs your ngrok domain in .env as PUBLIC_URL"

  command -v uv >/dev/null || die "uv isn't installed: brew install uv"
  if [[ -n $web ]]; then
    command -v pnpm >/dev/null || die "pnpm isn't installed: brew install pnpm"
    [[ -d $FRONTEND ]] || die "No frontend at $FRONTEND (set HEYDESK_FRONTEND to its folder)"
    [[ -d $FRONTEND/node_modules ]] || die "The frontend needs its packages: (cd $FRONTEND && pnpm install)"
  fi
  [[ $tunnel == ngrok ]] && ! command -v ngrok >/dev/null && die "ngrok isn't installed: brew install ngrok"
  [[ $tunnel == cloudflare ]] && ! command -v cloudflared >/dev/null && die "cloudflared isn't installed: brew install cloudflared"

  mkdir -p "$LOGS"
  echo "$what $args" >"$LOGS/last"

  # make room: an old copy would hold the port
  [[ -n $api ]] && free_port $API_PORT
  [[ -n $web ]] && free_port $WEB_PORT
  if [[ -n $tunnel ]] && tunnel_pids >/dev/null; then
    warn "Stopping the old tunnel"
    kill_all $(tunnel_pids)
  fi

  trap 'cleanup; exit 130' INT TERM
  trap cleanup EXIT

  if [[ -n $tunnel ]]; then
    # the link goes to the frontend when it runs, which passes /api on; else straight to the backend
    if [[ -n $web ]]; then start_tunnel $tunnel $WEB_PORT; else start_tunnel $tunnel $API_PORT; fi
    # the backend's links and Telegram, and the frontend's chat links, use this link
    export PUBLIC_URL=$LINK VITE_PUBLIC_URL=$LINK
  fi

  if [[ -n $api ]]; then
    printf '%sUpdating the database…%s\n' "$D" "$N"
    local out
    out=$(uv run alembic upgrade head 2>&1) || {
      echo "$out"
      die "The migration failed"
    }
    PYTHONUNBUFFERED=1 FORCE_COLOR=1 run api "$C" uv run uvicorn heydesk.main:app $reload --port $API_PORT
    local api_pid=$LAST_PID
  fi
  if [[ -n $web ]]; then
    FORCE_COLOR=1 run web "$M" pnpm --dir "$FRONTEND" run serve
    local web_pid=$LAST_PID
  fi

  [[ -n $api ]] && wait_for backend http://127.0.0.1:$API_PORT/health "$api_pid"
  [[ -n $web ]] && wait_for frontend http://127.0.0.1:$WEB_PORT "$web_pid"

  local line="${D}────────────────────────────────────────────────────────────${N}"
  echo
  echo "$line"
  printf ' %sHeyDesk is up%s   %smodel: %s · seed: %s%s\n' "$B$G" "$N" "$D" \
    "$(setting LLM_PROVIDER gigalogy)" "$(basename "$(setting DEV_AGENT_SEED shop)" .yaml)" "$N"
  [[ -n $api ]] && printf '   Backend    %shttp://localhost:%s%s   %s/ chat · /docs API%s\n' "$C" $API_PORT "$N" "$D" "$N"
  [[ -n $web ]] && printf '   Frontend   %shttp://localhost:%s%s\n' "$M" $WEB_PORT "$N"
  if [[ -n $LINK ]]; then
    local copied=""
    printf '%s' "$LINK" | copy && copied="  ${D}(copied)${N}"
    printf '   Public     %s%s%s%s\n' "$Y$B" "$LINK" "$N" "$copied"
  fi
  printf ' %sCtrl+C stops everything · logs: %s logs%s\n' "$D" "$SELF" "$N"
  echo "$line"

  if [[ -n $open ]]; then
    if [[ -n $LINK ]]; then browse "$LINK"; elif [[ -n $web ]]; then browse "http://localhost:$WEB_PORT"; else browse "http://localhost:$API_PORT"; fi
  fi
  wait
}

again() {
  [[ -f $LOGS/last ]] || die "Nothing was started yet"
  local last
  last=$(cat "$LOGS/last")
  ok "Again: $SELF $last"
  start $last
}

# ---------------------------------------------------------------- the other commands

stop_everything() {
  local any="" port
  for port in $API_PORT $WEB_PORT; do
    [[ -n $(listeners $port) ]] && free_port $port && any=1
  done
  if tunnel_pids >/dev/null; then
    warn "Stopping the tunnel"
    kill_all $(tunnel_pids)
    any=1
  fi
  if [[ -n $any ]]; then ok "Everything is stopped"; else ok "Nothing was running"; fi
}

show_status() {
  local name port pid link
  for name in Backend Frontend; do
    if [[ $name == Backend ]]; then port=$API_PORT; else port=$WEB_PORT; fi
    pid=$(listeners $port | head -1)
    if [[ -n $pid ]]; then
      printf '  %s●%s %-9s http://localhost:%s   %spid %s, %s%s\n' "$G" "$N" $name $port "$D" "$pid" "$(describe "$pid")" "$N"
    else
      printf '  %s○ %-9s not running%s\n' "$D" $name "$N"
    fi
  done
  if tunnel_pids >/dev/null; then
    link=$(tunnel_url)
    printf '  %s●%s %-9s %s\n' "$G" "$N" Public "${link:-starting…}"
  else
    printf '  %s○ %-9s no tunnel%s\n' "$D" Public "$N"
  fi
}

show_logs() {
  local name=${1:-}
  [[ -d $LOGS ]] || die "No logs yet: start something first"
  if [[ -n $name ]]; then
    [[ -f $LOGS/$name.log ]] || die "No $LOGS/$name.log (there are: $(ls $LOGS | grep log | tr '\n' ' '))"
    tail -n 50 -F "$LOGS/$name.log"
  else
    tail -n 20 -F "$LOGS"/*.log
  fi
}

reset_db() {
  warn "This deletes heydesk.db and everything in it, then makes the demo login."
  read -rp "Type yes to go on: " answer
  [[ $answer == yes ]] || die "Left as it was"
  [[ -n $(listeners $API_PORT) ]] && free_port $API_PORT # the backend holds the database open
  scripts/reset.sh
}

# ---------------------------------------------------------------- admins

admin() {
  case ${1:-} in
    "") admin_menu ;;
    list | ls) uv run heydesk admins ;;
    add | new | create)
      [[ -n ${2:-} ]] || die "Which email? $SELF admin add EMAIL [PASSWORD]"
      uv run heydesk create-admin "${@:2}"
      ;;
    password | pw | reset)
      [[ -n ${2:-} ]] || die "Which email? $SELF admin password EMAIL [PASSWORD]"
      uv run heydesk reset-password "${@:2}"
      ;;
    delete | rm)
      [[ -n ${2:-} ]] || die "Which email? $SELF admin delete EMAIL"
      uv run heydesk delete-admin "$2"
      ;;
    *) die "Unknown: admin $1 (list, add, password or delete)" ;;
  esac
}

admin_menu() {
  local k email password
  printf '\n %sAdmins%s\n' "$B" "$N"
  uv run heydesk admins | sed 's/^/  /'
  echo
  echo "  ${B}n${N} new admin    ${B}p${N} new password    ${B}x${N} delete    ${B}b${N} back    ${B}q${N} quit"
  read -rsn1 -p "  › " k
  echo
  case $k in
    n | p)
      read -rp "  Email: " email
      [[ -n $email ]] || admin_menu
      read -rp "  Password (↵ makes one up): " password
      if [[ $k == n ]]; then set -- create-admin; else set -- reset-password; fi
      if [[ -n $password ]]; then uv run heydesk "$1" "$email" "$password"; else uv run heydesk "$1" "$email"; fi
      ;;
    x)
      read -rp "  Email: " email
      read -rsn1 -p "  Delete the admin $email? y/↵ no  › " k && echo "$k"
      [[ $k == y ]] && uv run heydesk delete-admin "$email"
      ;;
    q) exit 0 ;;
    *) menu ;;
  esac
  admin_menu
}

seeds() { ls examples/agents | sed 's/\.yaml$//' | tr '\n' ' '; }

doctor() {
  local fails=0
  need() { # NAME FIX: a command that must exist
    if command -v "$1" >/dev/null; then ok "$1"; else
      warn "$1 is missing: $2"
      fails=1
    fi
  }
  nice() { # NAME FIX: a command that's good to have
    if command -v "$1" >/dev/null; then ok "$1"; else warn "$1 is missing (only for $2)"; fi
  }
  key() { # SETTING WHY: a key the chosen providers need
    if [[ -n $(setting "$1") ]]; then ok "$1 is set"; else
      warn "$1 is empty in .env ($2)"
      fails=1
    fi
  }
  echo "${B}Tools${N}"
  need uv "brew install uv"
  need pnpm "brew install pnpm"
  nice ngrok "a public link on your ngrok domain"
  nice cloudflared "a throwaway public link"

  echo "${B}Files${N}"
  if [[ -f .env ]]; then ok ".env"; else
    warn "No .env: cp .env.example .env, then fill in the keys"
    fails=1
  fi
  if [[ -d .venv ]]; then ok ".venv"; else warn "No .venv: uv sync"; fi
  if [[ -d $FRONTEND/node_modules ]]; then ok "frontend at $FRONTEND"; else warn "frontend: (cd $FRONTEND && pnpm install)"; fi

  echo "${B}Keys${N}"
  case $(setting LLM_PROVIDER gigalogy) in
    gigalogy) key GIGALOGY_API_KEY "LLM_PROVIDER=gigalogy" && key GIGALOGY_PROJECT_KEY "LLM_PROVIDER=gigalogy" ;;
    groq) key GROQ_API_KEY "LLM_PROVIDER=groq" ;;
  esac
  [[ $(setting EMBEDDER gemini) == gemini ]] && key GEMINI_API_KEY "EMBEDDER=gemini"
  [[ $(setting STT_PROVIDER deepgram) == deepgram ]] && key DEEPGRAM_API_KEY "voice calls"
  if [[ -n $(setting PUBLIC_URL) ]]; then ok "PUBLIC_URL: $(setting PUBLIC_URL)"; else warn "PUBLIC_URL is empty: public links use Cloudflare"; fi

  echo "${B}Database${N}"
  if uv run alembic current 2>/dev/null | grep -q "(head)"; then ok "migrations are up to date"; else warn "migrations are behind: $SELF migrate"; fi

  echo "${B}Running${N}"
  show_status
  return $fails
}

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

# ---------------------------------------------------------------- the menu

menu() {
  local key last="" tunnel_name
  [[ -f $LOGS/last ]] && last=$(cat "$LOGS/last")
  if [[ -n $(setting PUBLIC_URL) ]]; then tunnel_name="ngrok"; else tunnel_name="Cloudflare"; fi
  echo
  printf ' %sHeyDesk%s   %smodel: %s · seed: %s%s\n' "$B" "$N" "$D" \
    "$(setting LLM_PROVIDER gigalogy)" "$(basename "$(setting DEV_AGENT_SEED shop)" .yaml)" "$N"
  show_status
  echo
  echo "  ${B}1${N}  Backend"
  echo "  ${B}2${N}  Backend + frontend"
  echo "  ${B}3${N}  Backend + frontend + public link ${D}($tunnel_name)${N}"
  echo "  ${B}4${N}  Frontend only"
  echo "  ${B}5${N}  Choose: offline / Groq, another seed, which link…"
  [[ -n $last ]] && echo "  ${B}↵${N}  Again: ${D}$last${N}"
  echo
  echo "  ${B}s${N} stop all     ${B}i${N} status     ${B}l${N} logs       ${B}d${N} doctor"
  echo "  ${B}c${N} checks       ${B}t${N} tests      ${B}m${N} migrate    ${B}r${N} reset database"
  echo "  ${B}a${N} admins       ${B}h${N} help       ${B}q${N} quit"
  echo
  read -rsn1 -p "  › " key
  echo
  case $key in
    1) hint api && start api ;;
    2) hint all && start all ;;
    3) hint public && start all --tunnel ;;
    4) hint web && start web ;;
    5) custom ;;
    "") if [[ -n $last ]]; then again; else menu; fi ;;
    s) stop_everything && pause ;;
    i) show_status && pause ;;
    l) show_logs ;;
    d) doctor; pause ;;
    c) scripts/check.sh; pause ;;
    t) uv run pytest -q; pause ;;
    m) uv run alembic upgrade head && ok "Up to date"; pause ;;
    r) reset_db; pause ;;
    a) admin_menu ;;
    h) usage; pause ;;
    q) exit 0 ;;
    *) menu ;;
  esac
}

pause() {
  read -rsn1 -p "${D}  any key for the menu${N}"
  echo
  menu
}

hint() { printf '%s  next time, straight away: %s %s%s\n' "$D" "$SELF" "$1" "$N"; }

custom() {
  local what opts="" k
  read -rsn1 -p "  Run: 1 backend · 2 both · 3 frontend  › " k && echo "$k"
  case $k in 1) what=api ;; 3) what=web ;; *) what=all ;; esac
  if [[ $what != web ]]; then
    read -rsn1 -p "  Model: ↵ from .env ($(setting LLM_PROVIDER gigalogy)) · g Groq · f fake, offline  › " k && echo "$k"
    case $k in g) opts="$opts --groq" ;; f) opts="$opts --fake" ;; esac
    read -rp "  Seed: ↵ from .env ($(basename "$(setting DEV_AGENT_SEED shop)" .yaml)) or one of: $(seeds) › " k
    [[ -n $k ]] && opts="$opts --seed $k"
  fi
  read -rsn1 -p "  Public link: ↵ none · n ngrok · c Cloudflare  › " k && echo "$k"
  case $k in n) opts="$opts --ngrok" ;; c) opts="$opts --cf" ;; esac
  read -rsn1 -p "  Open the browser? y/↵ no  › " k && echo "$k"
  [[ $k == y ]] && opts="$opts --open"
  hint "$what$opts"
  start $what $opts
}

# ---------------------------------------------------------------- commands

cmd=${1:-}
shift 2>/dev/null
case $cmd in
  "") menu ;;
  api | b | back | backend) start api "$@" ;;
  web | f | front | frontend) start web "$@" ;;
  all | a | both) start all "$@" ;;
  public | p) start all --tunnel "$@" ;;
  again | .) again ;;
  stop | s | kill) stop_everything ;;
  status | st | i) show_status ;;
  logs | l) show_logs "$@" ;;
  check | c) exec scripts/check.sh ;;
  test | t) exec uv run pytest "$@" ;;
  migrate | m) uv run alembic upgrade head && ok "The database is up to date" ;;
  migration)
    [[ -n ${1:-} ]] || die "Name it: $SELF migration \"add photos\""
    uv run alembic revision --autogenerate -m "$1"
    ;;
  reset) reset_db ;;
  admin | admins) admin "$@" ;;
  doctor | d) doctor ;;
  help | h | -h | --help) usage ;;
  -*) start all "$cmd" "$@" ;; # the old way: dev.sh --tunnel
  *) die "Unknown command: $cmd (see $SELF help)" ;;
esac
