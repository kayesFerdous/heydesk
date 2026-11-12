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
io='fastapi|starlette|sqlalchemy|groq|httpx|livekit|pipecat'

forbid "domain/ is pure" $src/domain "${imp}(${io}|pydantic|heydesk\.(runtime|ports|shop|models|services|api|adapters|wiring|db))"
forbid "runtime/ is framework-free" $src/runtime "${imp}(${io}|heydesk\.(shop|models|services|api|adapters|wiring|db))"
forbid "tools reach the DB only through their store" $src/shop/tools "${imp}(${io}|heydesk\.(models|services|api|adapters|wiring|db))"
forbid "ports/ import nothing from the project" $src/ports "${imp}heydesk\."
forbid "only wiring.py imports adapters" "$src/runtime $src/shop $src/models $src/services $src/api" "${imp}heydesk\.adapters"
forbid "adapters/ know only ports and runtime" $src/adapters "${imp}heydesk\.(shop|models|services|api|wiring|db)"
forbid "models/ know only the database" $src/models "${imp}heydesk\.(runtime|ports|shop|services|api|adapters|wiring)"
forbid "the shop imports no services or api" $src/shop "${imp}heydesk\.(services|api|wiring)"
forbid "services/ import no api" $src/services "${imp}heydesk\.(api|wiring)"
forbid "only api/ knows HTTP" "$src/services $src/models $src/shop" "${imp}(fastapi|starlette)"
forbid "api/ and services/ call no other server themselves" "$src/api $src/services" "${imp}httpx"
forbid "only runtime/guards.py makes a Confirmed" "$src --exclude=guards.py --exclude=values.py" 'Confirmed(\[[^]]*\])?\('
if [ -d $src/voice ]; then
  forbid "voice/ is a listener: no DB, no tools" $src/voice "${imp}(sqlalchemy|heydesk\.(models|shop|adapters|db))"
fi
exit $fail
