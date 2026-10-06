# CLAUDE.md

HeyDesk is an **MVP**: a business creates an AI front-desk agent of one kind (a
restaurant, a clinic, a receptionist), edits it, and visitors talk to it by
web chat or voice. Each kind is a **desk**: one folder in `src/heydesk/desks/` with its own
prompt, tools, tables and routes (D41). A new kind is a new folder, not a new setting.
Async FastAPI, SQLAlchemy 2.0 async, Gigalogy's Maira (the default) or Groq (`openai/gpt-oss-120b`) for talking, Jev (TypeSafe) for
the commit check. Maira has no native tool calls, so its adapter asks for them
as JSON in the reply (D16).

**Keep it simple.** The owner is learning, and the code should read easily.
Build the normal path well; don't add code for edge cases that aren't worth it
(docs/design.md §12). If something seems to need extra protection, say so
instead of adding it.

## Start of a session

When the user says "do today's work" (or similar):

1. Read [PLAN.md](PLAN.md): the **Now** line and the next unticked day's bullets.
2. Read the parts of [docs/design.md](docs/design.md) that day touches, and build it.
3. Run `scripts/check.sh`, do the **Done when** check (ask the user if it needs
   them), tick the day, update **Now**, and say in a few lines what changed.

Read [docs/decisions.md](docs/decisions.md) only when changing a design.

**Prompt rules live in [docs/prompt-rules.md](docs/prompt-rules.md).** Any change
to what the model is told (platform rules, prompt sections, the state note) updates
that file in the same change.

If the code and design.md disagree, stop and say so. A design change updates
design.md **and** adds a line to decisions.md, in the same change.

## How we work

- One PLAN.md day at a time, in order. Tick a box only when its *Done when*
  check passes, and update the **Now** line.
- Code blocks in design.md are sketches: follow their shape and names, not every line.
- Commit only when asked. Before any commit: `scripts/check.sh`.
- **Commits and PRs have no AI attribution**: no `Co-Authored-By: Claude`
  trailer, no Claude or Anthropic email, no "Generated with Claude Code" line.
- `uv` runs everything. Never `pip`, never a bare `python`.
- `../Frontdesk` is a working reference (for example its Jev adapter).

## Commands

```bash
uv run alembic upgrade head                # once, and after every new migration
uv run uvicorn heydesk.main:app --reload   # / is the dev chat page
uv run pytest                              # offline and free
uv run pytest -m live                      # real Groq + Jev: costs quota, ask first
uv run alembic revision --autogenerate -m "…"
scripts/check.sh                           # pytest, ruff, format, mypy, import rules
```

## The rules (design.md §3)

1. **Python does facts**: prices, totals, dates, times. The model only talks.
2. **Read back, then a yes, then save.** A store's writing methods take only
   `Confirmed[T]`, made only by `confirm()` (Jev). It fails closed.
3. **Platform rules come first** in the prompt and aren't editable.
4. **The system prompt is built every turn** from the agent's config, with the
   channel, date and time at the end. No versions, no caching (D15).
5. **Messages are only added**, never edited.
6. **`workspace_id` on every table and query.**
7. **Every turn is traced.** A failure is a polite line and a hand-off.
8. **`runtime/` knows no framework, database or SDK.** Only `wiring.py` imports adapters.

## Conventions

- Python 3.12, async all the way down. SDK clients are built once in the lifespan.
- pydantic v2 for inputs: requests, config, knowledge, tool arguments.
- SQLAlchemy 2.0 typed (`Mapped[...]`), short sessions; never hold a
  transaction open while waiting on a model.
- Tools return problems as data (`ok=False`, a hint), not exceptions.
- Customer text is never passed to `str.format`; placeholders use `str.replace`.
- Logs are JSON with ids only: never message text, names or phone numbers.
- Errors that reach HTTP are `HeyDeskError` subclasses (`heydesk.errors`).
- Money in minor units; datetimes stored UTC; display and spoken forms come from `runtime/speech.py`.

## Testing

- Unit tests for `runtime/` and `domain/` with `FakeLLM`, `FakeDecider`
  and the fixed clock (`FIXED_NOW` = Tue 29 Sep 2026 08:00 UTC = 14:00 in Dhaka).
- A desk's tools are tested against its real store on in-memory SQLite: the `world` and
  `client` fixtures in `tests/conftest.py`, one test file per desk.
- Test the normal path and the failures that matter (no yes → no order), not every edge.

## Gotchas

- SQLAlchemy doesn't see in-place changes to JSON columns; assign a new value.
- gpt-oss reasoning counts as output tokens. An empty model reply is an error.
- Don't run tools when the model stopped for length: the arguments may be cut off.
- gpt-oss doesn't follow `$ref` in a tool schema; `Tool.spec` writes nested models out.
- Jev: no arithmetic or date comparisons; small state; positive questions.
- `ruff format` would rewrite code blocks in the docs; `*.md` is excluded. Don't run it on docs.
