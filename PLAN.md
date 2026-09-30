# HeyDesk — the plan

What to build next and how to know it's done. Specs live in [`docs/`](docs/)
(map in [CLAUDE.md](CLAUDE.md)); decisions in [docs/decisions.md](docs/decisions.md).

> **Now:** M0 done (30 Sep). **Next:** M1 day 2 (ports, fakes, `run_turn` text only, SSE chat page).
> **Target dates:** day 1 = Wed 30 Sep 2026 → demo on day 27 (Mon 26 Oct),
> buffer to day 30 (Thu 29 Oct).

**How to use this file**

- Work the next unticked day. Its **Read** column names the only doc sections
  it needs (`02 §7 *Structure*` = doc 02, section 7, subsection "Structure…").
- Tick a box only when its **Done when** check passes: tests green, or the
  thing visibly works. "Written" isn't done.
- Update the **Now** line when a day is ticked.

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

## M0 — Foundations (day 1) ✓

Done 30 Sep: pyproject + tool config, `Settings` + `.env.example`, `db.py` (`UTCDateTime`), `errors.py`, `main.py`, JSON `logs.py`, Alembic (async, batch), `/health`, `scripts/check.sh` + `check_imports.sh`, CI. The code is the record.

## M1 — The engine talks (days 2–7)

| Day | Build | Read | Done when |
|---|---|---|---|
| 2 | `ports/llm.py`, `ports/decider.py`; `FakeLLM`, `FakeDecider`; `runtime/events.py` (incl. `Progress`), `trace.py`; `run_turn` **text only**; `api/sse.py` + `POST /api/chat/{key}`; `web/static/` chat page | 02 §6 `LanguageModel`, §7 (up to *Structure*), §8, §14 · 03 §7 | [ ] Type in the browser, see fake tokens stream in |
| 3 | Models: workspace, agent, agent_version, conversation (with turn lease columns), turn (with `messages`, insert-only) + first migration; `AgentConfig`; presets loader; **restaurant** preset + knowledge + `examples/agents/restaurant.yaml`; `publish()` builds prompt + tool specs; `ensure_dev_agent()`; `start_turn` claims the lease; the turn is saved at its end; **greeting stored as the first assistant message** (D8) | 02 §4 (to *What a customer can edit*), §9, §10, §12 · 07 §10 | [ ] Restart: the dev agent exists; conversations and turns appear in the DB; the transcript = the turns' messages in order |
| 4 | `GroqLLM` (streaming, tool-call fragments joined, system + tools first, `reasoning_effort` `low`, timeouts); the context note (calendar); **current time in the state note** (D9) | 02 §6 `LanguageModel`, §7 *What the model sees* + *The system prompt* · 07 §2, §11 | [ ] Real chat about the menu; **cached tokens > 0** from turn 2; a trailing `system` message is followed |
| 5 | `tools/base.py` (`Tool` with `role`, `label`, `fillers`, `expected_ms`, `timeout_s`; `run_tool` validates → runs under its timeout → catches); `update_order`; the tool loop; `runtime/tasks.py` (detail types with `display()`/`spoken()`, `next_step`), `set_details`, the state note; **`runtime/speech.py`** (D7) | 02 §5, §7 *Structure* · 07 §1, §5 | [ ] "Two biryanis for 7:30, I'm Kayes" → the right total, computed in Python; the state note says *Still needed: phone*; the next reply asks for the phone and nothing else |
| 6 | **`runtime/progress.py`** + `watch()`; `Progress` → SSE `status`; the page's status line (D6). **Turn runner**: the turn runs in its own task, the SSE response subscribes; interrupt signal; `client_message_id` (D3, D10) | 07 §3, §4 · 02 §8 | [ ] "Checking…" appears while a tool runs; closing the tab mid-reply still saves the turn; a second message mid-reply gets 409 **without a model call** |
| 7 | Tests: every branch of `run_turn` (max steps, refusal, provider failure → `safe_close` + hand-off, interrupt), prompt byte-stability, progress policy with a fake clock, disconnect and 409 above. Catch up | 02 §16 · 07 §12 | [ ] Unit suite covers every branch of `run_turn` |

**Reuse from Frontdesk:** the SSE helper and stream reader (`channels/web/chat.py`, `static/index.html`), the trace (`core/trace.py`), the partial unique index (`adapters/sqlite/schema.sql`).

## M2 — Tools, guards, presets (days 8–14)

| Day | Build | Read | Done when |
|---|---|---|---|
| 8 | `JevDecider` (start from Frontdesk's `adapters/decider/jev.py`); `runtime/guards.py`: hand-off guard in parallel via `Gate`, **with a deadline, failing open** (D5); `handoff_to_human`; conversation `needs_human` | 02 §6 `Decider`, §7 (guards, `safe_close`) · 07 §9 · 01 §4 | [ ] "Can I talk to a person?" hands off; the trace shows the guard's timing; a Jev outage doesn't hand off every turn |
| 9 | `domain/values.py` (`Money`, `PhoneNumber`, `Confirmed[T]`); `ports/stores.py`; `orders` table; `place_order` with the commit check **on the heard text** and an idempotency key | 02 §3, §5 *Two tools in full*, §9 | [ ] Placing without a read-back → `not_confirmed`, no row. With a yes → one row, one number, even when retried. mypy rejects passing an unconfirmed value |
| 10 | `requests` table; `submit_request`; the `message` task; **receptionist** preset + seed with a custom task (`callback_request`); `AgentService.validate` rules for custom tasks and filler phrases | 02 §4 *Custom tasks*, §10 | [ ] Receptionist: leave a message, then a callback request → two rows, each only after a yes |
| 11 | `bookings` (partial unique index); `check_availability` (`expected_ms` set, fillers), `create_booking`; **clinic** preset + seed | 02 §5, §9 | [ ] Two conversations racing for one slot → exactly one booking |
| 12 | Scenarios: `tests/scenarios/<preset>/*.yaml` + one runner (scripted `FakeLLM`, or `-m live`), **including the experience assertions** (07 §12) | 02 §16 · 07 §12 | [ ] A live pass rate per preset, written in the README |
| 13–14 | Failure phrases (07 §9); tune platform rules and presets from live failures; `heydesk chat` (terminal); README "how to run"; latency P50/P95 from traces | 07 §9 · 02 §14 | [ ] Live pass rate went up and every remaining failure has a known cause; latency numbers in the README |

## M3 — The SaaS backend (days 15–21)

| Day | Build | Read | Done when |
|---|---|---|---|
| 15 | `users`, signup/login (pwdlib + pyjwt), `current_workspace` | 02 §13 | [ ] A token gets you your workspace id |
| 16 | `GET /api/presets` (+ knowledge JSON Schema); `POST /api/agents`; `GET/PATCH /api/agents/{id}` with per-field errors | 02 §4, §11 | [ ] Create and edit an agent in Swagger (`/docs`) |
| 17 | `POST /api/agents/{id}/publish`, versions | 02 §7 *The system prompt*, §10 | [ ] Editing the draft doesn't change the live agent until publish; open conversations keep their version |
| 18 | Dashboard reads: conversations (turns, traces, progress lines, heard vs generated), orders, bookings, inbox; status changes through model methods | 02 §10, §11, §14 | [ ] Accept an order and mark it ready via the API |
| 19 | `usage_events` per turn | 02 §9, §14 | [ ] One row per turn; a SQL `sum` gives cost per conversation |
| 20 | Tenant-isolation tests on every dashboard route; CORS; `MAX_TURNS`; consistent error shapes; export the OpenAPI schema | 02 §13, §16 | [ ] Workspace A gets 404 on everything of B's |
| 21 | Buffer. Stretch: CI job running the integration tests on Postgres | 06 §3 | |

## M4 — Voice (days 22–27)

Spec: [04](docs/04-voice-later.md) and [07](docs/07-conversation-experience.md) §5–§8. The core
is already voice-ready after M1–M2. This milestone is the channel.

| Day | Build | Read | Done when |
|---|---|---|---|
| 22 | **Spike**: a browser call that echoes back, on LiveKit Agents *and/or* Pipecat; pick one + STT + TTS providers; record it as a decision (Q3, Q4) | 04 §2, §4 | [ ] You talk, it answers; a decision logged with the measured first-sound time |
| 23 | `voice/pipeline.py`: final transcript → `start_turn(channel="voice")` → `voice/sentences.py` → TTS; a **Call** button on the dev page | 04 §3 · 07 §4 | [ ] **Place one order by voice** |
| 24 | Voice context profile + spoken forms end to end; `Progress` lines spoken (the channel drops them while audio plays); `voice/speakable.py`; keyword boosting from knowledge | 07 §3, §5, §6 | [ ] "Anything Friday evening?" → you hear a wait line, then times read as words |
| 25 | Barge-in: interrupt signal + heard text + barge-in note; backchannel settings; commit check on heard text | 07 §4, §7 | [ ] Interrupting a list of three times after the first → the next turn knows only the first was heard; a cut-off read-back + "yes" commits nothing |
| 26 | Silence reprompt, `end_call`, AI disclosure line; voice marks in the trace | 07 §8 · 04 §4 | [ ] Last word → first sound P50/P95 measured and written in the README |
| 27 | Voice scenarios text-in/text-out (`channel="voice"`); **demo deploy** (Docker image, SQLite volume, secrets in env, `APP_ENV=production`, HTTPS so the browser allows the mic) | 04 §5 · 07 §12 | [ ] Smoke test end to end on the server: chat, and a voice order |

Stretch (only if ahead): a phone number through the framework's SIP/telephony, with caller ID pre-filling the phone detail (04 §3).

## After the month

In order:

1. **The Vue app** (week 4 of the original plan: `git show e690664:docs/05-one-month-plan.md`). Vite + Vue 3 + TS + Router + Pinia, types generated from the OpenAPI schema; login, agents list, create from preset, editor (basics, tools, knowledge form), publish, `/c/{public_key}` chat page, dashboard with trace view. Then delete `web/`.
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

Moved to [docs/decisions.md](docs/decisions.md) (D1–D13). Read it when changing a design or when a `D#` is cited and the doc section doesn't explain it.

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
