# HeyDesk

An AI front desk a business can build in minutes. It picks a preset
(restaurant host, receptionist), fills in its menu, hours and FAQ, edits the
instructions. Visitors talk to the agent by web chat or by
voice; it answers questions, takes orders and messages, and hands over to a
person when needed.

The model writes the words; code decides every price, total and time, and
nothing is saved without reading it back and hearing a yes.

## Status

Days 1–4 are done: streamed chat with Groq, Gigalogy or a fake model, agents
and conversations saved, the restaurant preset. See **[PLAN.md](PLAN.md)**.

## Docs

| | |
|---|---|
| [PLAN.md](PLAN.md) | What to build, day by day, and how to know it's done |
| [docs/design.md](docs/design.md) | How it works |
| [docs/decisions.md](docs/decisions.md) | Why it works that way |
| [CLAUDE.md](CLAUDE.md) | Commands, rules and conventions |

## Running it

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn heydesk.main:app --reload   # open http://localhost:8000
scripts/check.sh                           # tests, lint, format, types, import rules
```

## Voice latency

The time from the caller's last word to the agent's first sound. To measure
it, keep the server's log, make about ten calls, then run the report:

```bash
uv run uvicorn heydesk.main:app 2>&1 | tee voice.log
uv run python scripts/latency.py voice.log
```

| | P50 | P95 |
|---|---|---|
| Last word → first sound | not measured yet | not measured yet |

So far: 2.1s on one echo call (2 Oct, no model). Chorus takes 1.8–2.4s of that.
