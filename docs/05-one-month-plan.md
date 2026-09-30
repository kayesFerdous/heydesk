# 05 — The one-month plan

Three weeks of backend, one week of Vue, a few buffer days. Each week ends
with something that runs and a check you can verify.

**The goal is an MVP to show your mentor, built so it can become production
grade without a rewrite.** So this plan builds everything that's expensive to
retrofit (tenancy, agent versions, the commit check, the ports, traces, usage
records, migrations) and leaves out everything that's cheap to add later
(Postgres, caching, the eval harness, rate limits and budgets, background
jobs, fallbacks). [06-production-and-scaling.md](06-production-and-scaling.md)
lists each deferred item, the hook it plugs into, and why adding it later is a
small change. If day 1 is Wednesday
30 September 2026, deploy day (day 27) is Monday 26 October, and days 28–30
(27–29 October) are buffer.

Everything referenced here is specified in [02-build-guide.md](02-build-guide.md)
and laid out in [03-project-structure.md](03-project-structure.md). What comes
after the month (caching, the job lane, scaling out) is in
[06-production-and-scaling.md](06-production-and-scaling.md).

---

## The shape of the month

| Week | Theme | At the end you can… |
|---|---|---|
| 1 | **The engine talks** | Chat in the browser with the `.env` restaurant agent; it recommends from the menu and builds an order with correct totals; every turn is saved with a trace |
| 2 | **Tools, guards, presets** | Switch `.env` between restaurant, receptionist and clinic; orders, bookings and messages are saved **only after a confirmed read-back**; hand-off works; you have a live pass rate |
| 3 | **The SaaS backend** | Through the API: sign up → create an agent from a preset → edit → publish → chat via its public key → see the order in the dashboard API. Workspace A can't touch B. |
| 4 | **Vue + ship** | Do all of the above in the browser, on a deployed server |

---

## Week 1: the engine talks

| Day | Build | Check |
|---|---|---|
| 1 | Dependencies; `main.py` + lifespan; `config.py`; `db.py` (async engine, `UTCDateTime`); `alembic init -t async`; `/health`; pytest + httpx + ruff; `check_imports.sh` | `uv run pytest` green; `/health` 200 |
| 2 | `ports/llm.py`, `ports/decider.py`; `FakeLLM`, `FakeDecider`; `runtime/events.py`, `trace.py`; `run_turn` **text only**; `api/sse.py` + `POST /api/chat/{key}`; `web/static/` chat page | Type in the browser, see fake tokens stream in |
| 3 | Models: workspace, agent, agent_version, conversation, turn + first migration; `AgentConfig`; presets loader; **restaurant** preset + knowledge model + `examples/agents/restaurant.yaml`; `publish()` builds prompt + tool specs; `ensure_dev_agent()`; `handle_turn` saves transcript + turn | Restart, the dev agent exists; conversations and turns appear in the DB |
| 4 | `GroqLLM` (streaming, tool-call fragments joined, system + tools first for the automatic cache, `reasoning_effort` `low`); the context note | Real chat about the menu. The trace shows **cached tokens > 0** from the second turn. A trailing `system` message is followed (the state note depends on it) |
| 5 | `tools/base.py` (`Tool`, `ToolContext`, `run_tool` with pydantic validation); `update_order`; the tool loop in `run_turn`; `tool` events in the page. **Tasks**: `runtime/tasks.py` (detail types, `next_step`), `set_details`, the state note | "Two biryanis for 7:30, I'm Kayes": the right total, computed in Python, and the trace's state note says *Still needed: phone*. The next reply asks for the phone number, and nothing else |
| 6–7 | Tests: loop edge cases (max steps, refusal, provider failure → `safe_close` + hand-off), optimistic lock → 409, prompt byte-stability. Catch up. | Unit suite covers every branch of `run_turn` |

**Reuse from Frontdesk:** the SSE helper and streaming page reader
(`channels/web/chat.py`, `static/index.html`), the trace idea
(`core/trace.py`), and the partial unique index pattern (`adapters/sqlite/schema.sql`).

---

## Week 2: tools, guards, presets

| Day | Build | Check |
|---|---|---|
| 8 | `JevDecider` (start from Frontdesk's `adapters/decider/jev.py`); `runtime/guards.py`: the hand-off guard **in parallel** (`Gate`); `handoff_to_human` tool; conversation `needs_human` | "Can I talk to a person?" hands off; the trace shows the guard finished before the first token |
| 9 | `domain/values.py` (`Money`, `PhoneNumber`, `Confirmed[T]`); `ports/stores.py`; `orders` table; `place_order` (details from state, `next_step` must be `Confirm`) with the **commit check** (`confirm()` → `Confirmed`) and idempotency key | Placing without a read-back → `not_confirmed`, no row. With a yes → one row, one order number, even if the request is retried |
| 10 | `requests` table; `submit_request`; the `message` task; **receptionist** preset + seed with one **custom task** (e.g. `callback_request`); `AgentService.validate` rules for custom tasks | Switch `.env` to receptionist: leave a message, then make a callback request. Two rows in `requests`, each with its details, each only after a yes |
| 11 | `bookings` (partial unique index); `check_availability`, `create_booking`; **clinic** preset + seed | Two conversations racing for one slot: exactly one booking |
| 12 | Scenario tests per preset: `tests/scenarios/<preset>/*.yaml` (visitor lines + expected rows) and one test that plays them with scripted `FakeLLM`, or `-m live` against Groq + Jev | A live pass rate per preset, written in the README |
| 13–14 | Tune platform rules and preset instructions from what the live runs got wrong; `heydesk chat` in the terminal; README "how to run" | Live pass rate went up, and you know why each remaining failure fails |

---

## Week 3: the SaaS backend

| Day | Build | Check |
|---|---|---|
| 15 | `users`, signup/login (pwdlib + pyjwt), `current_workspace` | A token gets you your workspace id |
| 16 | `GET /api/presets` (+ knowledge JSON Schema); `POST /api/agents` (create from preset); `GET/PATCH /api/agents/{id}` with per-field validation errors | Create and edit an agent in Swagger (`/docs`) |
| 17 | `POST /api/agents/{id}/publish`, versions | Edit the draft: the published agent doesn't change until you publish; open conversations keep their version |
| 18 | Dashboard reads: conversations (with turns + traces), orders, bookings, inbox; status changes through model methods | Accept an order and mark it ready via the API |
| 19 | `usage_events` written per turn (read nothing from it yet) | One row per turn with tokens and cost; a SQL `sum` gives cost per conversation |
| 20 | Tenant-isolation tests on every dashboard route; CORS; `MAX_TURNS`; consistent error shapes; export the OpenAPI schema for Vue | Workspace A gets 404 on everything of B's |
| 21 | Buffer | |

---

## Week 4: Vue, and ship

Use Vite + Vue 3 + TypeScript + Vue Router + Pinia, a component library you
like (to save a week of CSS), and TypeScript types generated from the OpenAPI
schema so the frontend and backend can't drift.

| Day | Build |
|---|---|
| 22 | Project setup, API client + types, login/signup, auth guard on routes |
| 23 | Agents list; "create from preset" (preset cards → name + business basics) |
| 24 | Agent editor: basics (name, greeting, instructions), tools (toggles + settings + note), knowledge (per-preset form; the restaurant menu as an editable table) |
| 25 | Publish button; a shareable chat page `/c/{public_key}` (the same fetch-stream reader as `web/chat.js`) |
| 26 | Dashboard: conversations with a turn-by-turn trace view, orders, bookings, inbox |
| 27 | Demo deploy: one Docker image on a small server, SQLite on a volume, secrets in env, `APP_ENV=production`, `alembic upgrade head` on start; delete `web/`; smoke test end to end |
| 28–30 | Buffer: bugs, polish, demo data for each preset |

---

## If you fall behind: the cut list

Cut from the top. Each cut keeps everything below it working.

1. The deploy (demo from your laptop)
2. The custom task in the receptionist seed (keep the message task)
3. Bookings + the clinic preset (ship restaurant + receptionist)
4. The Vue agent editor's knowledge form (edit knowledge as YAML in a text area)

**Never cut:** the commit check, tenant isolation and its tests, the trace,
agent versions pinned to conversations, and `usage_events`. Each of these is cheap now and very
expensive to bolt on after customers depend on the system.

---

## Habits for the month

- **Before every commit:** `uv run pytest`, `uv run ruff check .`, `./scripts/check_imports.sh`.
- **After every prompt change:** `uv run pytest -m live tests/scenarios/<preset>`.
  Compare the pass rate, not your impression.
- **Once a week:** read ten real traces end to end. Check cached tokens, time
  to first token, cost per conversation. That's where the next day's work comes from.
- **Every bug from a real chat** becomes a scenario YAML before it's fixed.

## Risks to watch

| Risk | Early sign | What to do |
|---|---|---|
| The model ignores a preset instruction | Live scenarios fail on the same step | Move the rule into a tool (validation or an error hint); prompts suggest, tools enforce |
| Cost per conversation too high for your pricing | `usage_events` after week 3 | Check the cache is hitting first; then try a cheaper model per agent; it's a setting |
| Jev commit check too strict or too loose | `not_confirmed` on clear yeses, or a pass on "hmm maybe" | Tune the 0.7 thresholds from logged answers, not by feel |
| The Vue week slips | Day 24 and the editor isn't done | Ship the knowledge form as a JSON/YAML text area with server-side validation; make it pretty later |
| Groq rate limits (429) | `LLMUnavailable` hand-offs in traces during tests or the first real traffic | Check your plan's tokens-per-minute limit; run live scenarios one preset at a time; a paid tier and `FallbackLLM` belong to the production phase (06) |
| Scope creep ("just add voice", "just add caching", "just add Postgres") | — | 04 and 06 are the plans for after the MVP. Not this month. |
