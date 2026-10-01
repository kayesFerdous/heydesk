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
