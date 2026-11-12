# CLAUDE.md

HeyDesk is an **MVP**: an online shop creates an AI front-desk agent, edits it, and
visitors talk to it by web chat or the messaging apps. It serves only online shops: everything
about the shop is in `src/heydesk/shop/`, with no layer for other kinds of business (D172).
Async FastAPI, SQLAlchemy 2.0 async, Gigalogy's Maira (the default) or Groq (`openai/gpt-oss-120b`) for talking
and for judging the visitor's yes (D107). Maira has no native tool calls, so its adapter asks for them
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
- A day that builds a gap from [docs/competitor-gaps.md](docs/competitor-gaps.md)
  (G1, G2, …) also ticks that gap there, with the date and D-number.
- Code blocks in design.md are sketches: follow their shape and names, not every line.
- Every feature or fix gets its own branch (`feat/<short-name>`,
  `fix/<short-name>`), never work on `main`. When the work is done, push the
  branch and open a PR with a short, simple title.
- Commit only when asked. Before any commit: `scripts/check.sh`.
- **Commits and PRs have no AI attribution**: no `Co-Authored-By: Claude`
  trailer, no Claude or Anthropic email, no "Generated with Claude Code" line.
- `uv` runs everything. Never `pip`, never a bare `python`.
- `../Frontdesk` is a working reference.

## Commands

```bash
uv run alembic upgrade head                # once, and after every new migration
uv run uvicorn heydesk.main:app --reload   # / is the dev chat page
uv run pytest                              # offline and free
uv run pytest -m live                      # real Groq: costs quota, ask first
uv run alembic revision --autogenerate -m "…"
scripts/check.sh                           # pytest, ruff, format, mypy, import rules
```

## The rules (design.md §3)

1. **Python does facts**: prices, totals, dates, times. The model only talks.
2. **Read back, then a yes, then save.** A tool saves what the visitor gave only
   through store methods that take a `Confirmed[T]`, made only by `confirm()`: the
   model judges the yes, Python checks the details are the ones read back on an
   earlier turn. It fails closed. (The owner's own routes write without one.)
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
- Comments say what and why in plain words. Decision and gap numbers (D…, G…) live in
  `docs/` only, never in code, config or tests.
- Customer text is never passed to `str.format`; placeholders use `str.replace`.
- Logs are JSON with ids only: never message text, names or phone numbers.
- Errors that reach HTTP are `HeyDeskError` subclasses (`heydesk.errors`).
- Money in minor units; datetimes stored UTC; display and spoken forms come from `runtime/speech.py`.

## Testing

- Unit tests for `runtime/` and `domain/` with `FakeLLM`
  and the fixed clock (`FIXED_NOW` = Tue 29 Sep 2026 08:00 UTC = 14:00 in Dhaka).
- The shop's tools are tested against its real store on in-memory SQLite: the `world` and
  `client` fixtures in `tests/conftest.py`.
- Test the normal path and the failures that matter (no yes → no order), not every edge.

## Gotchas

- SQLAlchemy doesn't see in-place changes to JSON columns; assign a new value.
- gpt-oss reasoning counts as output tokens. An empty model reply is an error.
- Don't run tools when the model stopped for length: the arguments may be cut off.
- gpt-oss doesn't follow `$ref` in a tool schema; `Tool.spec` writes nested models out.
- `ruff format` would rewrite code blocks in the docs; `*.md` is excluded. Don't run it on docs.

## Project explorer sync
When you change code, update docs/project-explorer.html in the same task.
1. Read docs/project-explorer.index.md and find the section IDs mapped to the files you changed.
2. Find line ranges with grep -n "SECTION:<id>" docs/project-explorer.html. Read and edit only those ranges. Never read the full HTML.
3. Prefer editing the data-* JSON blocks. Touch view code only when a new kind of visual is needed.
4. New module/agent/tool/prompt → add it to the matching JSON block. New section → add markers and an index row.
