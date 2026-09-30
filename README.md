# HeyDesk

A SaaS where businesses build their own AI front-desk agent from a preset
(restaurant host, receptionist, clinic front desk), edit its instructions,
knowledge and tools, publish it, and put it in front of their customers by web
chat and, from week 4, by voice.

The agent is **natural but deterministic**: the model writes the words, while
code decides what to collect, when to commit, and every price, time and total.
Nothing is ordered or booked without a read-back and a yes. Nobody is left
waiting in silence while it checks something.

## Status

M0 (foundations) is done: config, database, JSON logs, `/health`, checks and CI. See **[PLAN.md](PLAN.md)** for the
milestones and progress.

## Docs

| | |
|---|---|
| [PLAN.md](PLAN.md) | What to build next, how to know it's done, what to read for it |
| [docs/decisions.md](docs/decisions.md) | The decision log (D1–D13) |
| [CLAUDE.md](CLAUDE.md) | How to work in this repo: commands, rules, conventions |
| [docs/02](docs/02-build-guide.md) | The engine: rules, tools, ports, one turn, data model, API |
| [docs/07](docs/07-conversation-experience.md) | The experience: latency, fillers, interruptions, spoken forms |
| [docs/03](docs/03-project-structure.md) | Folder layout and import rules |
| [docs/04](docs/04-voice-later.md) | The voice channel |
| [docs/06](docs/06-production-and-scaling.md) | From MVP to production |
| [docs/01](docs/01-frontdesk-analysis.md) | What we took from Frontdesk |

## Running it

```bash
cp .env.example .env
uv sync
uv run uvicorn heydesk.main:app --reload   # GET /health
scripts/check.sh                           # tests, lint, format, types, import rules
```
