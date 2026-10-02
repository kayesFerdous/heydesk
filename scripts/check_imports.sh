#!/usr/bin/env bash
# The layer rules of docs/design.md §4, as greps. A layer that doesn't exist yet passes.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
forbid() {  # message, paths, pattern
  if hits=$(grep -rnE "$3" $2 2>/dev/null); then echo "FAIL $1"; echo "$hits"; fail=1; else echo "ok   $1"; fi
}
imp='^[[:space:]]*(from|import)[[:space:]]+'
src=src/heydesk
io='fastapi|starlette|sqlalchemy|groq|httpx|typesafe_sdk|livekit|pipecat'

forbid "domain/ is pure" $src/domain "${imp}(${io}|pydantic|heydesk\.(runtime|ports|tools|presets|models|services|api|adapters|wiring|db))"
forbid "runtime/ is framework-free" $src/runtime "${imp}(${io}|heydesk\.(presets|tools|models|services|api|adapters|wiring|db))"
forbid "tools/ reach the DB only via ctx.services" $src/tools "${imp}(${io}|heydesk\.(models|services|api|adapters|wiring|db))"
forbid "presets/ are pure" $src/presets "${imp}(${io}|heydesk\.(models|services|api|adapters|tools|wiring|db))"
forbid "ports/ import nothing from the project" $src/ports "${imp}heydesk\."
forbid "only wiring.py imports adapters" "$src/runtime $src/tools $src/presets $src/models $src/services $src/api" "${imp}heydesk\.adapters"
forbid "services/ and models/ don't know HTTP" "$src/services $src/models" "${imp}(fastapi|starlette)"
forbid "only runtime/guards.py makes a Confirmed" "$src --exclude=guards.py --exclude=values.py" 'Confirmed\('
if [ -d $src/voice ]; then
  forbid "voice/ is a listener: no DB, no tools" $src/voice "${imp}(sqlalchemy|heydesk\.(models|tools|adapters|db))"
fi
exit $fail
