# CLAUDE.md

HeyDesk is a SaaS where a business creates its own AI front-desk agent from a
preset (restaurant, receptionist, clinic), edits it, publishes it, and puts it
in front of its customers: web chat now, voice from M4. Async FastAPI, SQLAlchemy
2.0 async, Groq (`openai/gpt-oss-120b`) for talking, Jev (TypeSafe) for typed
judgments. Built as an MVP that becomes production without a rewrite.

## Start here

- **[PLAN.md](PLAN.md)**: the *Now* line, the next unticked task, its **Done
  when** check, the decision log and the open questions. Read it first.
- The spec: [02](docs/02-build-guide.md) the engine, [07](docs/07-conversation-experience.md)
  the conversation experience, [03](docs/03-project-structure.md) layout and import rules,
  [04](docs/04-voice-later.md) voice, [06](docs/06-production-and-scaling.md) MVP → production,
  [01](docs/01-frontdesk-analysis.md) lessons from Frontdesk (`../Frontdesk`, a working reference).
- **The docs are the spec.** If code and a doc disagree, stop and say so; don't
  silently pick one. A design change updates the doc **and** adds a decision-log
  line in PLAN.md, in the same change.

## How we work

- One PLAN.md task at a time, in order. Agree the scope with the user before
  starting a new milestone. Tick a box only when its *Done when* check passes.
- Code blocks in the docs are sketches: follow their shape and names
  (`ToolResult`, `TurnEnded`, `start_turn`…), not every line.
- Commit only when asked. Before any commit: `scripts/check.sh`.
- `uv` runs everything. Never `pip`, never a bare `python`.

## Commands (from M0 on)

```bash
uv run uvicorn heydesk.main:app --reload   # dev server; / is the minimal chat page
uv run heydesk chat                        # terminal chat with the .env dev agent
uv run pytest                              # unit + integration + scripted scenarios: offline, free
uv run pytest -m live tests/scenarios/<preset>   # real Groq + Jev: costs quota, ask first
uv run alembic revision --autogenerate -m "…" && uv run alembic upgrade head
scripts/check.sh                           # pytest, ruff check, ruff format --check, mypy, import rules
```

## Non-negotiables

Full list and reasons: 02 §3 and 07.

1. **The model talks; code guarantees.** Prices, totals, dates, availability,
   validation and "it was saved" are decided in Python inside tools. The model
   never does arithmetic or date maths; it reads what tools return.
2. **Nothing is committed without a read-back and a yes.** Writing services
   accept only `Confirmed[T]`, and only `confirm()` (the Jev commit check, on the
   *heard* text) makes one. It fails closed.
3. **Customers edit the words, not the guarantees.** Platform rules come first
   in the prompt and aren't editable.
4. **A conversation is pinned to one agent version.** The system prompt and
   tool specs are built at publish, stored, and sent byte-for-byte. Nothing that
   varies (date, time, channel) goes in the system prompt: use the context note
   or the state note.
5. **Transcripts are append-only.** Turns are insert-only; the transcript is
   every turn's `messages` in order. Never edit a stored message; append a note.
6. **`workspace_id` on every row and every query.** Tools reach the database
   only through `ctx.services`, already scoped.
7. **Every turn is traced, including failures.** A failure is a polite scripted
   line plus a hand-off: never silence, never a stack trace.
8. **`runtime/` knows no framework, database or SDK.** Only `wiring.py` imports adapters.
9. **Bounded:** `MAX_STEPS` 6 model calls per turn, `max_completion_tokens`,
   a timeout on every tool, `MAX_TURNS` per conversation.
10. **One turn at a time per conversation, and it always finishes.** The turn
    lease is claimed first (409 otherwise). The turn runs in its own task;
    listeners subscribe. A disconnect doesn't stop it; a barge-in *signals* it.
11. **Scripted moments are code.** Greeting, progress lines, hand-off, apologies.
    Progress lines never enter the transcript, and the model is told never to say
    "let me check" itself.
12. **Values:** money in minor units; datetimes stored UTC, "today" is the
    business's timezone; display and spoken forms come from `runtime/speech.py`,
    picked by channel in code.

## Layers (checked by `scripts/check_imports.sh`)

`domain/` pure values · `ports/` Protocols only · `runtime/` the engine ·
`adapters/` provider implementations (imported only by `wiring.py`) · `tools/`
the tool library (DB only via `ctx.services`) · `presets/` data + pydantic ·
`models/` tables · `services/` the only code that opens sessions and commits ·
`api/` read the request, call one service, return · `voice/` a listener, like
`api/chat.py`. Where new code goes: 03 §4.

## Conventions

- Python 3.12, async all the way down. No blocking calls in async code (ruff's
  `ASYNC` rules catch many). SDK clients are built once in the lifespan.
- pydantic v2 for every input: requests, config, knowledge, tool arguments.
- SQLAlchemy 2.0 typed ORM (`Mapped[...]`), short sessions; **never hold a
  transaction open while waiting on a model**. Portable SQL only (SQLite now,
  Postgres later): SQLAlchemy types, `UTCDateTime`, Alembic `render_as_batch`.
- Tools return problems as data (`ok=False`, `error`, `hint`); exceptions are bugs.
- Customer text is never passed to `str.format`; placeholders use `str.replace`
  on a fixed list.
- Logs are JSON with ids only: never message text, names or phone numbers.
- mypy strict on `domain/`, `ports/`, `runtime/`, `tools/`. `Confirmed[T]` is
  only a guarantee if the types are checked.

## Testing

- Every behaviour in `domain/`, `runtime/` and `tools/` gets a unit test with
  `FakeLLM`, `FakeDecider` and the fixed clock (`FIXED_NOW` = Tue 29 Sep 2026
  08:00 UTC = 14:00 in Dhaka).
- Integration tests use in-memory SQLite through `httpx.ASGITransport`.
- Scenarios are YAML **data**, graded on outcomes (rows written, nothing before
  a yes) and experience assertions (07 §12), never on exact wording.
- Every bug from a real chat becomes a scenario YAML before it's fixed.

## Gotchas

- Starlette cancels a streaming response when the client disconnects, so a
  turn must never run inside the response generator (D3).
- SQLAlchemy doesn't see in-place changes to JSON columns; assign a new value.
- Groq caches an exact prefix. If `cached` tokens drop to zero after turn 1,
  something changed the system prompt or tool specs.
- gpt-oss reasoning counts as output tokens and delays the first token. An
  empty model reply is an error, not "the model said nothing".
- Don't run tools when `finish_reason` is `length`: the arguments may be cut off.
- Jev: no arithmetic, counting or date comparisons; small focused state;
  positively worded questions; a `none` option on every Choice.
- "I'd like to see someone about a rash" is not a request for a human. Word
  hand-off questions carefully.
- Changing a detail clears what `depends_on` it (read back Wednesday, booked
  Tuesday). `next_step()` is the one function for both "what do we ask next?"
  and "can we commit?".
