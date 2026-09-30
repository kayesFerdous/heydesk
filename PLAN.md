# HeyDesk — the plan

The one file we follow to make progress. Specs live in [`docs/`](docs/); this
file says **what to build next, how to know it's done, and what we decided**.

> **Now:** planning done, code not started. **Next:** M0 (foundations).
> **Target dates:** day 1 = Wed 30 Sep 2026 → demo on day 27 (Mon 26 Oct),
> buffer to day 30 (Thu 29 Oct).

**How to use this file**

- Tick a box only when its **Done when** check passes: tests green, or the
  thing visibly works. "Written" isn't done.
- Changing the design? Add a line to the [decision log](#decision-log) and update
  the doc it touches **in the same commit**. Docs are the spec; they must not drift.
- Before every commit: `scripts/check.sh` (from M0 on).
- Every bug found in a real chat becomes a scenario YAML *before* it's fixed.

| Doc | What it's for |
|---|---|
| [01](docs/01-frontdesk-analysis.md) | What we took from Frontdesk, and why |
| [02](docs/02-build-guide.md) | **The engine spec**: rules, tools, ports, the turn, data model, API |
| [03](docs/03-project-structure.md) | Folder layout and import rules |
| [04](docs/04-voice-later.md) | The voice channel (M4) |
| [06](docs/06-production-and-scaling.md) | MVP → production: what's deferred and where it plugs in |
| [07](docs/07-conversation-experience.md) | **The experience spec**: natural + deterministic, fillers, interruptions, spoken forms |

---

## The shape of the month

| Milestone | Days | Theme | At the end you can… |
|---|---|---|---|
| **M0** | 1 | Foundations | Run `scripts/check.sh` green; `/health` is 200; logs are JSON |
| **M1** | 2–7 | The engine talks | Chat in the browser with the `.env` restaurant agent. It builds an order with correct totals, shows "Checking…" status lines, never greets twice, and survives a closed tab |
| **M2** | 8–14 | Tools, guards, presets | Switch `.env` between restaurant, receptionist and clinic. Orders, bookings and messages are saved **only after a confirmed read-back**; hand-off works; you have a live pass rate and latency numbers |
| **M3** | 15–21 | The SaaS backend | Through the API: sign up → create an agent → edit → publish → chat → see the order in the dashboard API. Workspace A can't touch B |
| **M4** | 22–27 | **Voice** | Talk to the agent in the browser: it answers in speech, says "let me check" while tools run, can be interrupted, and places an order. Deployed |
| Buffer | 28–30 | | Bugs, polish, demo data |

**After the month:** the Vue app (builder and dashboard), then the production
phase (06 §3). See [after the month](#after-the-month).

---

## M0 — Foundations (day 1)

- [ ] `pyproject.toml`: runtime deps (02 §2), dev deps (`pytest`, `pytest-asyncio`, `httpx`, `asgi-lifespan`, `ruff`, `mypy`), `heydesk = "heydesk.cli:main"`, pytest/ruff/mypy config (03 §8)
- [ ] `config.py` (`Settings`, production validator) + `.env.example` (02 §15)
- [ ] `db.py` (async engine, session factory, `Base`, `UTCDateTime`), `errors.py`, `main.py` (`create_app` + lifespan), `wiring.py` skeleton
- [ ] `logs.py`: JSON log lines with `request_id`, `workspace_id`, `conversation_id`, `turn` from contextvars. **Never message text or phone numbers** (D12)
- [ ] Alembic: `alembic init -t async`, `render_as_batch=True`
- [ ] `GET /health`
- [ ] `scripts/check_imports.sh` (03 §2) and `scripts/check.sh` (pytest, ruff check, ruff format --check, mypy, imports)
- [ ] CI workflow running `scripts/check.sh` (once the repo has a GitHub remote)

**Done when:** `scripts/check.sh` is green; `/health` returns 200; a request logs one JSON line with a `request_id`.

## M1 — The engine talks (days 2–7)

| Day | Build | Done when |
|---|---|---|
| 2 | `ports/llm.py`, `ports/decider.py`; `FakeLLM`, `FakeDecider`; `runtime/events.py` (incl. `Progress`), `trace.py`; `run_turn` **text only**; `api/sse.py` + `POST /api/chat/{key}`; `web/static/` chat page | [ ] Type in the browser, see fake tokens stream in |
| 3 | Models: workspace, agent, agent_version, conversation (with turn lease columns), turn (with `messages`, insert-only) + first migration; `AgentConfig`; presets loader; **restaurant** preset + knowledge + `examples/agents/restaurant.yaml`; `publish()` builds prompt + tool specs; `ensure_dev_agent()`; `start_turn` claims the lease; the turn is saved at its end; **greeting stored as the first assistant message** (D8) | [ ] Restart: the dev agent exists; conversations and turns appear in the DB; the transcript = the turns' messages in order |
| 4 | `GroqLLM` (streaming, tool-call fragments joined, system + tools first, `reasoning_effort` `low`, timeouts); the context note (calendar); **current time in the state note** (D9) | [ ] Real chat about the menu; **cached tokens > 0** from turn 2; a trailing `system` message is followed |
| 5 | `tools/base.py` (`Tool` with `role`, `label`, `fillers`, `expected_ms`, `timeout_s`; `run_tool` validates → runs under its timeout → catches); `update_order`; the tool loop; `runtime/tasks.py` (detail types with `display()`/`spoken()`, `next_step`), `set_details`, the state note; **`runtime/speech.py`** (D7) | [ ] "Two biryanis for 7:30, I'm Kayes" → the right total, computed in Python; the state note says *Still needed: phone*; the next reply asks for the phone and nothing else |
| 6 | **`runtime/progress.py`** + `watch()`; `Progress` → SSE `status`; the page's status line (D6). **Turn runner**: the turn runs in its own task, the SSE response subscribes; interrupt signal; `client_message_id` (D3, D10) | [ ] "Checking…" appears while a tool runs; closing the tab mid-reply still saves the turn; a second message mid-reply gets 409 **without a model call** |
| 7 | Tests: every branch of `run_turn` (max steps, refusal, provider failure → `safe_close` + hand-off, interrupt), prompt byte-stability, progress policy with a fake clock, disconnect and 409 above. Catch up | [ ] Unit suite covers every branch of `run_turn` |

**Reuse from Frontdesk:** the SSE helper and stream reader (`channels/web/chat.py`, `static/index.html`), the trace (`core/trace.py`), the partial unique index (`adapters/sqlite/schema.sql`).

## M2 — Tools, guards, presets (days 8–14)

| Day | Build | Done when |
|---|---|---|
| 8 | `JevDecider` (start from Frontdesk's `adapters/decider/jev.py`); `runtime/guards.py`: hand-off guard in parallel via `Gate`, **with a deadline, failing open** (D5); `handoff_to_human`; conversation `needs_human` | [ ] "Can I talk to a person?" hands off; the trace shows the guard's timing; a Jev outage doesn't hand off every turn |
| 9 | `domain/values.py` (`Money`, `PhoneNumber`, `Confirmed[T]`); `ports/stores.py`; `orders` table; `place_order` with the commit check **on the heard text** and an idempotency key | [ ] Placing without a read-back → `not_confirmed`, no row. With a yes → one row, one number, even when retried. mypy rejects passing an unconfirmed value |
| 10 | `requests` table; `submit_request`; the `message` task; **receptionist** preset + seed with a custom task (`callback_request`); `AgentService.validate` rules for custom tasks and filler phrases | [ ] Receptionist: leave a message, then a callback request → two rows, each only after a yes |
| 11 | `bookings` (partial unique index); `check_availability` (`expected_ms` set, fillers), `create_booking`; **clinic** preset + seed | [ ] Two conversations racing for one slot → exactly one booking |
| 12 | Scenarios: `tests/scenarios/<preset>/*.yaml` + one runner (scripted `FakeLLM`, or `-m live`), **including the experience assertions** (07 §12) | [ ] A live pass rate per preset, written in the README |
| 13–14 | Failure phrases (07 §9); tune platform rules and presets from live failures; `heydesk chat` (terminal); README "how to run"; latency P50/P95 from traces | [ ] Live pass rate went up and every remaining failure has a known cause; latency numbers in the README |

## M3 — The SaaS backend (days 15–21)

| Day | Build | Done when |
|---|---|---|
| 15 | `users`, signup/login (pwdlib + pyjwt), `current_workspace` | [ ] A token gets you your workspace id |
| 16 | `GET /api/presets` (+ knowledge JSON Schema); `POST /api/agents`; `GET/PATCH /api/agents/{id}` with per-field errors | [ ] Create and edit an agent in Swagger (`/docs`) |
| 17 | `POST /api/agents/{id}/publish`, versions | [ ] Editing the draft doesn't change the live agent until publish; open conversations keep their version |
| 18 | Dashboard reads: conversations (turns, traces, progress lines, heard vs generated), orders, bookings, inbox; status changes through model methods | [ ] Accept an order and mark it ready via the API |
| 19 | `usage_events` per turn | [ ] One row per turn; a SQL `sum` gives cost per conversation |
| 20 | Tenant-isolation tests on every dashboard route; CORS; `MAX_TURNS`; consistent error shapes; export the OpenAPI schema | [ ] Workspace A gets 404 on everything of B's |
| 21 | Buffer. Stretch: CI job running the integration tests on Postgres | |

## M4 — Voice (days 22–27)

Spec: [04](docs/04-voice-later.md) and [07](docs/07-conversation-experience.md) §5–§8. The core
is already voice-ready after M1–M2. This milestone is the channel.

| Day | Build | Done when |
|---|---|---|
| 22 | **Spike**: a browser call that echoes back, on LiveKit Agents *and/or* Pipecat; pick one + STT + TTS providers; record it as a decision (Q3, Q4) | [ ] You talk, it answers; a decision logged with the measured first-sound time |
| 23 | `voice/pipeline.py`: final transcript → `start_turn(channel="voice")` → `voice/sentences.py` → TTS; a **Call** button on the dev page | [ ] **Place one order by voice** |
| 24 | Voice context profile + spoken forms end to end; `Progress` lines spoken (the channel drops them while audio plays); `voice/speakable.py`; keyword boosting from knowledge | [ ] "Anything Friday evening?" → you hear a wait line, then times read as words |
| 25 | Barge-in: interrupt signal + heard text + barge-in note; backchannel settings; commit check on heard text | [ ] Interrupting a list of three times after the first → the next turn knows only the first was heard; a cut-off read-back + "yes" commits nothing |
| 26 | Silence reprompt, `end_call`, AI disclosure line; voice marks in the trace | [ ] Last word → first sound P50/P95 measured and written in the README |
| 27 | Voice scenarios text-in/text-out (`channel="voice"`); **demo deploy** (Docker image, SQLite volume, secrets in env, `APP_ENV=production`, HTTPS so the browser allows the mic) | [ ] Smoke test end to end on the server: chat, and a voice order |

Stretch (only if ahead): a phone number through the framework's SIP/telephony, with caller ID pre-filling the phone detail (04 §3).

## After the month

In order:

1. **The Vue app**: 05's old week 4. Vite + Vue 3 + TS + Router + Pinia, types generated from the OpenAPI schema; login, agents list, create from preset, editor (basics, tools, knowledge form), publish, `/c/{public_key}` chat page, dashboard with trace view. Then delete `web/`.
2. **The production phase**: [06 §3](docs/06-production-and-scaling.md), items 1–11 (Postgres, rate limits, budgets, observability, fallback LLM, eval harness…).
3. **Phone numbers and warm transfer**, if not done as the M4 stretch.
4. **Voice extras**: [07 §13](docs/07-conversation-experience.md), last row.

---

## If you fall behind: the cut list

Cut from the top. Each cut keeps everything below it working.

1. The deploy (demo from your laptop)
2. Voice polish: silence reprompt, `end_call`, disclosure (keep: talking, fillers, barge-in)
3. The custom task in the receptionist seed (keep the message task)
4. Bookings + the clinic preset (ship restaurant + receptionist)
5. Dashboard reads for bookings and inbox (keep conversations and orders)

**Never cut:** the commit check; tenant isolation and its tests; the trace;
agent versions pinned to conversations; `usage_events`; the turn lease and
listener-independent turns; the progress policy. Each is cheap now and very
expensive to bolt on after customers depend on the system.

---

## Decision log

Newest last. Each entry: what, why, and which doc holds the detail.

| # | Date | Decision | Why | Detail |
|---|---|---|---|---|
| D1 | 2026-09-30 | **Voice channel in M4 (week 4); the Vue app moves after the month.** The voice-ready core goes into M1–M2 | Voice is the product's end goal and proves "one brain, many channels"; Swagger is enough of a dashboard for the demo | This file; 04 |
| D2 | 2026-09-30 | **Turn lease at the start of a turn** (conditional `UPDATE`), plus the version check at save | A version check alone notices a concurrent turn only *after* both ran the model and tools | 02 §8, 07 §4 |
| D3 | 2026-09-30 | **A turn runs in its own task; listeners subscribe. Interruption is a signal** | Starlette cancels a streaming generator on disconnect, which would lose a turn after `place_order` wrote. Barge-in must stop speech without losing the turn | 02 §7 §10, 07 §4 |
| D4 | 2026-09-30 | **The transcript is stored per turn** (`turns.messages`, insert-only); `conversations.transcript` is dropped | Append-only by construction, no growing blob rewritten every turn, and the review page shows exactly what the model saw | 02 §9 |
| D5 | 2026-09-30 | **The hand-off guard fails open after a deadline; the commit check fails closed** | Otherwise a Jev outage or slow answer hands off every turn or delays every first word | 02 §6, 07 §9 |
| D6 | 2026-09-30 | **`Progress` events from a code policy**: fixed phrases, deterministic rotation, never in the transcript; web shows labels, voice speaks text | "Let me check that" must be instant, identical and safe, and must not be answered by the model later | 07 §3 |
| D7 | 2026-09-30 | **Display and spoken forms for every value** (`runtime/speech.py`); the channel picks in code | Models convert numbers to words unreliably; the read-back on voice must be exact | 07 §5 |
| D8 | 2026-09-30 | **The greeting is stored as the first assistant message** | Otherwise the model doesn't know the visitor saw it, and greets twice | 07 §10 |
| D9 | 2026-09-30 | **Current time in the state note**; a calendar note is appended if the date changes | "Are you open now?" must stay right; the first-turn context note goes stale | 07 §11 |
| D10 | 2026-09-30 | **`client_message_id`** on every chat message, unique per conversation | A retry after a network blip must not run a turn (and an order) twice | 02 §8, 07 §4 |
| D11 | 2026-09-30 | **mypy and CI from M0** | `Confirmed[T]` makes an unconfirmed write a *type error* only if a type checker runs | 03 §8 |
| D12 | 2026-09-30 | **Structured JSON logs from M0** (ids only, no message text or phone numbers) | Log context is painful to retrofit; PII rules are easiest from the first line | 06 §3 #4 |
| D13 | 2026-09-30 | **Every tool has `timeout_s`; `run_tool` enforces it** | "Checking another source" must never mean a hung conversation | 02 §5, 07 §3 |

---

## Open questions

| # | Question | Needed by |
|---|---|---|
| Q1 | Groq API key, and which tier? The free tier's tokens-per-minute limit throttles live scenario runs | Day 4 |
| Q2 | TypeSafe (Jev) access and API key? If not, an `LlmDecider` has to come forward from 06 | Day 8 |
| Q3 | Voice framework: **LiveKit Agents** (built-in turn detector, interruption handling, SIP, hosted option) or **Pipecat** (pipeline control, many transports). Decide by the day-22 spike, on first-sound latency and how cleanly our `start_turn` plugs in as the LLM step | Day 22 |
| Q4 | STT and TTS providers (e.g. Deepgram for STT; Cartesia or ElevenLabs Flash for TTS) and a monthly budget | Day 22 |
| Q5 | Demo hosting with HTTPS (browsers only allow the microphone on HTTPS or localhost) | Day 27 |
| Q6 | What does your mentor most want to see: the voice demo, the builder UI, or the engineering (traces, tests, guarantees)? | Before M3 |

---

## Habits

- **Before every commit:** `scripts/check.sh`.
- **After every prompt change:** `uv run pytest -m live tests/scenarios/<preset>`. Compare the pass rate, not your impression.
- **Once a week:** read ten real traces end to end: cached tokens, `ttft_ms`, `first_output_ms`, progress lines, cost per conversation. The next day's work comes from there.
- **Every bug from a real chat** becomes a scenario YAML before it's fixed.

## Risks to watch

| Risk | Early sign | What to do |
|---|---|---|
| The model ignores a preset instruction | Live scenarios fail on the same step | Move the rule into a tool (validation or an error hint). Prompts suggest; tools enforce |
| Voice latency over target | First answer P50 > 2 s in M4 | 07 §2 mitigations in order: ack lines, `gpt-oss-20b` for voice agents, then the single-call optimisation |
| The model says "let me check" itself | `no_self_filler` fails | Tighten the platform rule. The policy already skips its line when the model spoke first |
| Cost per conversation too high | `usage_events` after M3 | Check the cache is hitting; then a cheaper model per agent |
| Jev commit check too strict or too loose | `not_confirmed` on clear yeses, or a pass on "hmm maybe" | Tune the 0.7 thresholds from logged answers, not by feel |
| The voice framework eats the week | Day 23 and no order by voice | Cut list item 2; keep the pipeline thin: transcript in, sentences out |
| Groq rate limits (429) | `LLMUnavailable` hand-offs in traces | Check the plan's limits; run live scenarios one preset at a time; `FallbackLLM` is in 06 |
| Scope creep ("just add caching / Postgres / Vue") | — | 06 and "after the month" are the plan for those. Not this month |
