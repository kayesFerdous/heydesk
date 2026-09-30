# 03 — Project structure (v3)

The layout for HeyDesk as an **async FastAPI SaaS**: presets and editable
agents, a tool library, a model-driven runtime behind two ports (Groq for
talking, Jev for judging), a minimal chat page now and a Vue app later.

---

## 1. The tree

```
heydesk/
├── pyproject.toml
├── .env.example                  every setting, one-line comment each (committed)
├── alembic.ini
├── README.md
├── docs/                         01–06
├── migrations/                   Alembic, async template (generated, committed)
├── scripts/
│   └── check_imports.sh          the layer rules below, as greps
├── examples/
│   └── agents/                   seed files for DEV_AGENT_SEED: one business per preset
│       ├── restaurant.yaml
│       ├── receptionist.yaml
│       └── clinic.yaml
├── frontend/                     Vue 3 + Vite (week 4). Its own package.json; talks to /api only.
│
├── src/heydesk/
│   ├── main.py                   create_app() + lifespan; `uv run heydesk` starts uvicorn
│   ├── config.py                 Settings
│   ├── wiring.py                 build_llm(), build_decider(), build_services(): the ONLY file that imports adapters
│   ├── db.py                     async engine, session factory, Base, UTCDateTime
│   ├── errors.py                 HeyDeskError family + exception handlers
│   ├── cli.py                    `heydesk chat` (terminal), `heydesk create-user`
│   │
│   ├── domain/                   PURE VALUES. Imports nothing from the project.
│   │   └── values.py             Money, PhoneNumber, Confirmed[T]: invalid values can't be built
│   │
│   ├── runtime/                  THE ENGINE. No FastAPI, no SQLAlchemy, no provider SDKs.
│   │   ├── loop.py               run_turn(): guard ∥ model ↔ tools, events, safe_close
│   │   ├── events.py             Opened, Token, ToolStarted, ToolFinished, HandOff, TurnEnded
│   │   ├── config.py             AgentConfig, ToolSetting, ModelSettings, Handoff (pydantic)
│   │   ├── prompt.py             build_system_prompt(): platform rules → instructions → knowledge → FAQ (called at publish)
│   │   ├── context.py            the first-turn context note: channel style + date/time
│   │   ├── tasks.py              TaskDef, detail types (phone, date, slot…), next_step(), read_back(), depends_on clearing
│   │   ├── state_note.py         the note sent last on every model call: have / still needed / next
│   │   ├── guards.py             Jev hand-off guard, commit check
│   │   ├── schedule.py           WeeklyHours, business-local "today", slot grids
│   │   ├── entities.py           phone normalisation, money formatting, date validation
│   │   └── trace.py              per-turn steps, timings, usage
│   │
│   ├── ports/                    INTERFACES. Protocols + their data types, nothing else.
│   │   ├── llm.py                LanguageModel, ChatRequest, ToolSpec, TextDelta, ToolCall, Finished, LLMUnavailable
│   │   ├── decider.py            Decider, Choice, YesNo, Score, Answer, DeciderUnavailable
│   │   └── stores.py             OrderStore, BookingStore, RequestStore, ToolServices: what tools may ask of the DB
│   │
│   ├── adapters/                 IMPLEMENTATIONS of the ports. Imported only by wiring.py.
│   │   ├── llm/
│   │   │   ├── groq.py           GroqLLM (default, openai/gpt-oss-120b)
│   │   │   └── fake.py           FakeLLM: scripted steps
│   │   └── decider/
│   │       ├── jev.py            JevDecider (default)
│   │       └── fake.py           FakeDecider
│   │
│   ├── tools/                    THE TOOL LIBRARY. What an agent can do.
│   │   ├── __init__.py           TOOLS registry
│   │   ├── base.py               Tool, ToolContext, ToolResult, run_tool() (validate → run → catch)
│   │   ├── details.py            set_details: fill, validate and clear task details
│   │   ├── orders.py             update_order, place_order
│   │   ├── bookings.py           check_availability, create_booking
│   │   ├── requests.py           submit_request: finishes messages and every custom task
│   │   └── handoff.py            handoff_to_human
│   │
│   ├── presets/                  AGENT PRESETS. Data + a knowledge model each. Pure.
│   │   ├── __init__.py           load_presets() → PRESETS: dict[str, Preset]
│   │   ├── restaurant/
│   │   │   ├── preset.yaml       label, greeting, instructions, allowed + default tools, tasks
│   │   │   └── knowledge.py      RestaurantKnowledge (pydantic) + render()
│   │   ├── receptionist/         same two files
│   │   └── clinic/               same two files
│   │
│   ├── models/                   TABLES. Every one has workspace_id.
│   │   ├── workspace.py          Workspace, User
│   │   ├── agent.py              Agent, AgentVersion
│   │   ├── conversation.py       Conversation, Turn
│   │   ├── order.py              Order (+ status methods)
│   │   ├── booking.py            Booking (+ status methods, partial unique index)
│   │   ├── request.py            Request (+ status methods)
│   │   └── usage.py              UsageEvent
│   │
│   ├── services/                 USE CASES. The only code that opens sessions and commits.
│   │   ├── __init__.py           Services dataclass
│   │   ├── conversations.py      handle_turn(): the ONE entry point for every channel
│   │   ├── agents.py             create_from_preset, update_draft, publish, published_by_key, ensure_dev_agent
│   │   ├── orders.py
│   │   ├── bookings.py
│   │   ├── requests.py
│   │   └── auth.py
│   │
│   ├── api/                      HTTP. Read the request, call one service, return.
│   │   ├── __init__.py           include_routers(app)
│   │   ├── deps.py               get_services, current_workspace (JWT)
│   │   ├── sse.py                events → text/event-stream
│   │   ├── chat.py               GET/POST /api/chat/{public_key}   (public)
│   │   ├── auth.py               /api/auth/*
│   │   ├── presets.py            /api/presets
│   │   ├── agents.py             /api/agents, publish, versions
│   │   ├── conversations.py      /api/conversations
│   │   ├── orders.py · bookings.py · requests.py
│   │   └── health.py
│   │
│   └── web/                      THE MINIMAL CHAT PAGE (dev only; retired when Vue lands)
│       └── static/
│           ├── index.html
│           └── chat.js           fetch() + streamed SSE reader, ~40 lines
│
└── tests/
    ├── conftest.py               app, async client, SQLite in memory, fixed clock, FakeLLM, FakeDecider
    ├── unit/
    │   ├── runtime/              test_loop, test_prompt (byte-stable), test_guards, test_entities, test_tasks, test_state_note, test_history
    │   ├── tools/                test_orders, test_bookings, test_requests
    │   ├── presets/              each preset loads; example seed validates; render deterministic
    │   └── adapters/             groq chunk mapping, jev mapping
    ├── integration/              test_chat_api, test_agents_api, test_tenant_isolation, test_bookings_db, test_conflict_409
    └── scenarios/                <preset>/*.yaml (visitor lines + expected rows) + one runner test; FakeLLM by default, `-m live` for Groq + Jev
```

About 60 Python files by the end of week 3. Every file has one job you can say
in a sentence.

---

## 2. The layers, and which way imports point

```
        api/            cli.py          voice/ (later)
          │               │               │
          └───────────────┼───────────────┘
                          ▼
                      services/ ─────────────► models/ ──► db.py
                       │     │
                       │     └──► tools/ ──► (services via ToolContext, never imported)
                       ▼            │
                   runtime/ ◄───────┘
                       │  ▲
                       ▼  └── presets/ (data + pydantic; uses runtime/schedule types)
                    ports/ ◄──── adapters/          wiring.py builds adapters and
                                                    hands them to services as ports
```

| Layer | May import | Must NOT import |
|---|---|---|
| `domain/` | stdlib | anything else in the project |
| `runtime/` | `domain/`, `ports/`, stdlib, `pydantic` | `presets/`, `tools/`, `fastapi`, `sqlalchemy`, `groq`, `typesafe_sdk`, `models/`, `services/`, `api/`, `adapters/`. It is handed the stored prompt, tool specs and a tool runner. |
| `ports/` | `domain/`, stdlib, `pydantic` | anything else in the project |
| `adapters/` | `ports/`, the provider SDK | `runtime/`, `tools/`, `models/`, `services/`, `api/`, `fastapi` |
| `tools/` | `domain/`, `ports/`, `runtime/` (config, entities, schedule, guards, tasks), `pydantic` | `fastapi`, `sqlalchemy`, `models/`, `adapters/`, `api/`. Tools reach the database **only** through `ctx.services`. |
| `presets/` | `runtime/schedule` (shared types), `pydantic` | everything with I/O |
| `models/` | `db`, `errors` | `services/`, `api/`, `fastapi` |
| `services/` | `runtime/`, `tools/`, `presets/`, `ports/`, `models/`, `db` | `api/`, `fastapi`, `adapters/` |
| `api/` | `services/`, `runtime/` types, `fastapi` | `adapters/`, `models/` for writing |
| `wiring.py`, `main.py` | everything | — |

Why `tools/` doesn't import services directly: a tool is handed a
`ToolContext` whose `services` are already scoped to one workspace, typed by
the Protocols in `ports/stores.py`. So a tool **can't** forget the workspace,
the import arrow never points back up, and a unit test hands it a fake.

### The rules, checked

`scripts/check_imports.sh`:

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
forbid() {  # message, paths, pattern
  if hits=$(grep -rnE "$3" $2); then echo "FAIL $1"; echo "$hits"; fail=1; else echo "ok   $1"; fi
}
imp='^[[:space:]]*(from|import)[[:space:]]+'
src=src/heydesk
io='fastapi|starlette|sqlalchemy|groq|typesafe_sdk'

forbid "domain/ is pure" $src/domain "${imp}(${io}|pydantic|heydesk\.(runtime|ports|tools|presets|models|services|api|adapters|wiring|db))"
forbid "runtime/ is framework-free" $src/runtime "${imp}(${io}|heydesk\.(presets|tools|models|services|api|adapters|wiring|db))"
forbid "tools/ reach the DB only via ctx.services" $src/tools "${imp}(${io}|heydesk\.(models|services|api|adapters|wiring|db))"
forbid "presets/ are pure" $src/presets "${imp}(${io}|heydesk\.(models|services|api|adapters|tools|wiring|db))"
forbid "ports/ import nothing from the project" $src/ports "${imp}heydesk\."
forbid "only wiring.py imports adapters" "$src/runtime $src/tools $src/presets $src/models $src/services $src/api" "${imp}heydesk\.adapters"
forbid "services/ and models/ don't know HTTP" "$src/services $src/models" "${imp}(fastapi|starlette)"
exit $fail
```

---

## 3. One message, end to end

```
POST /api/chat/{public_key}  {message, conversation_id}
  api/chat.py                  validate → services.agents.published_by_key() → StreamingResponse(handle_turn(...))
  services/conversations.py    [session 1] load conversation + its PINNED agent version; build TurnInput
  runtime/loop.py              guard ∥ model.stream() → tokens out → tool calls
  tools/orders.py              validate args → ctx.services.orders.place() [session 2, scoped] → result
  runtime/loop.py              results back to the model → final text → TurnEnded
  services/conversations.py    [session 3] append transcript, state, turn, usage (optimistic lock)
  api/sse.py                   token / tool / handoff / done events to the browser
```

A phone call later is the same list with `voice/` at the top instead of `api/chat.py`.

---

## 4. Where does new code go?

| You want to… | Put it in |
|---|---|
| A new kind of agent (salon, hotel…) | `presets/<id>/preset.yaml` + `knowledge.py`, and `examples/agents/<id>.yaml` |
| A new action agents can take | a file in `tools/`, registered in `TOOLS`, allowed by the presets that need it |
| Change what the model is always told | `runtime/prompt.py` (platform rules), with a byte-stability test |
| Change a preset's default wording or tools | its `preset.yaml` (affects **new** agents only) |
| A new question for Jev | `runtime/guards.py` |
| A new task for a preset (e.g. "quote request") | the preset's `tasks:` in `preset.yaml` + a finishing tool if none fits |
| A new kind of detail (email, address…) | a detail type in `runtime/tasks.py`, with tests |
| A new model provider | `adapters/llm/<name>.py` + a branch in `wiring.py` |
| A new judgment provider | `adapters/decider/<name>.py` + a branch in `wiring.py` |
| A dashboard feature | a service method, then a route in `api/` |
| A table or column | `models/` + an Alembic migration |
| A setting | `config.py` **and** `.env.example` |
| A terminal command | `cli.py` |
| A bug from a real conversation | a scenario test that reproduces it first |

Smell tests:
- The word "menu" or "doctor" outside `presets/`, `tools/` or `examples/`
  means it's in the wrong place.
- A `workspace_id` that isn't passed in, but looked up inside a tool, is a
  tenancy bug waiting to happen.

---

## 5. Why this shape

**Why `presets/` are data, not code.** A preset is a *starting point the
customer copies and edits*. Its instructions, greeting and default tools must
be plain values that get copied into `agent.draft`. Only the knowledge *form*
(a pydantic model) and its renderer are code, because they define structure.

**Why tools are code and customers only toggle them.** A tool is where the
guarantees live (prices, availability, the commit check). Customers choose
which tools are on and tune their settings. A custom webhook tool later lets
them add actions without touching this code.

**Why the prompt is built at publish, not per turn.** `services/agents.publish()`
renders the knowledge with the preset, calls `runtime/prompt.build_system_prompt()`,
and stores the result and the tool specs on the version. Turns send stored
bytes, so a byte-stable prompt is a fact of the design, not something a test
has to keep catching. It also keeps `runtime/` from importing `presets/`
(presets import `runtime/schedule`, so the other direction would be a cycle).

**Why `runtime/` is framework-free.** It holds the hardest logic (the loop,
the guard race, safe closing), so it should be testable with plain `await`
calls and fakes. It's also what voice will reuse unchanged.

**Why these ports, and no more.** The model and the decider have real
alternatives (Groq / fallback host / fake; Jev / LLM / fake). `stores.py`
exists for a different reason: dependency direction. Tools must not import
services, so they depend on a Protocol the services implement. Everywhere
else, services use the SQLAlchemy session directly; it already is a
repository. A cache, a job queue or a notifier becomes a port the day it has a
second implementation (06), not before; that was Frontdesk's rule too.

**Why `domain/` is small.** Frontdesk's value objects earned their place:
`Money` can't be negative, `PhoneNumber` is always valid, and `Confirmed[T]`
makes rule 2 a type error. HeyDesk keeps those. It doesn't copy the pure
aggregates and in-memory repositories: order and booking status methods live
on the SQLAlchemy models, and tests use SQLite in memory. That's one mapping
layer fewer for a CRUD-heavy SaaS.

**Why `wiring.py`.** Web, CLI, tests and voice later all build the system
the same way, and `main.py`'s lifespan creates the SDK clients **once** (warm
connections), then closes them on shutdown.

**Why agent versions exist from day one.** A conversation running on a config
that changes mid-chat misses Groq's prefix cache and makes the review page
untrustworthy. One extra table fixes both, and
"draft / publish" is what a builder UI needs anyway.

**Why `web/` is a separate little folder.** It's scaffolding for the first two
weeks. When the Vue app can chat, you delete `web/` and the `/` route, and
nothing else notices.

---

## 6. From Flask to FastAPI

What you know from `learn_flask`, and what it becomes here:

| learn_flask (Flask) | HeyDesk (FastAPI) |
|---|---|
| `create_app()` | `create_app()` in `main.py`, plus a `lifespan` context for start-up and shutdown |
| `Blueprint` | `APIRouter` |
| `app.extensions["services"]` | `app.state.services`, read through a `Depends(get_services)` |
| flask-smorest + marshmallow schemas | pydantic models as parameters and `response_model` |
| `@app.errorhandler(StoreAPIError)` | `app.add_exception_handler(HeyDeskError, handler)` |
| flask-jwt-extended | a `current_workspace` dependency using pyjwt |
| Flask-SQLAlchemy `db.session` | `async_sessionmaker` from `db.py`; `async with sessions() as s:` |
| Flask-Migrate (`flask db upgrade`) | Alembic directly (`alembic upgrade head`) |
| `flask` CLI commands | `cli.py` (plain `argparse`, or `typer` if you like it) |
| RQ worker | nothing this month; a `heydesk worker` process in the production phase (06) |
| `gunicorn` | `uvicorn` (or gunicorn with uvicorn workers) |

The layering you learned (routes → services → models, errors raised by
services and turned into JSON in one place, one wiring function) carries over
unchanged.

---

## 7. Wiring

```python
# src/heydesk/main.py
def create_app(settings: Settings | None = None, *, llm=None, decider=None, now=utcnow) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, sessions = make_engine(settings.DATABASE_URL)
        llm_, decider_ = llm or build_llm(settings), decider or build_decider(settings)
        app.state.services = build_services(sessions, llm_, decider_, settings, now)
        if settings.APP_ENV == "development":
            await app.state.services.agents.ensure_dev_agent(settings.DEV_AGENT_PRESET, settings.DEV_AGENT_SEED)
        yield
        await close_all(llm_, decider_)          # SDK clients have connection pools
        await engine.dispose()

    app = FastAPI(title="HeyDesk", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.CORS_ORIGINS, allow_methods=["*"],
                       allow_headers=["*"])
    register_exception_handlers(app)
    include_routers(app)
    if settings.APP_ENV == "development":
        app.mount("/", StaticFiles(directory=WEB_STATIC, html=True), name="web")   # the minimal page
    return app

app = create_app()
```

```python
# tests/conftest.py
FIXED_NOW = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)          # Tuesday, 14:00 in Dhaka

@pytest.fixture
async def client():
    app = create_app(Settings(APP_ENV="test", DATABASE_URL="sqlite+aiosqlite://"),
                     llm=FakeLLM(), decider=FakeDecider(), now=lambda: FIXED_NOW)
    async with LifespanManager(app):                           # or call the lifespan by hand
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as c:
            yield c
```

(`asgi-lifespan` provides `LifespanManager`. Add it as a dev dependency, or
start the lifespan yourself in the fixture.)

---

## 8. `pyproject.toml`

```toml
[project]
name = "heydesk"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn[standard]",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite",
    "alembic",
    "pydantic-settings",
    "groq",
    "typesafe-sdk",
    "pyjwt",
    "pwdlib[argon2]",
    "pyyaml",
    "tzdata",
]

[project.scripts]
heydesk = "heydesk.cli:main"

[dependency-groups]
dev = ["pytest", "pytest-asyncio", "httpx", "asgi-lifespan", "ruff"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
markers = ["live: calls real providers (needs GROQ_API_KEY and TYPESAFE_API_KEY)"]
addopts = "-m 'not live'"

[tool.ruff]
line-length = 100

[tool.ruff.lint]
extend-select = ["I", "B", "UP", "SIM", "ASYNC"]
```

Let `uv add` pin the versions. `ASYNC` lint rules catch blocking calls
(`time.sleep`, sync HTTP) inside async code, the most common async bug.

Everyday commands:

```bash
uv run uvicorn heydesk.main:app --reload
uv run heydesk chat
uv run alembic revision --autogenerate -m "…"
uv run alembic upgrade head
uv run pytest
uv run pytest -m live                      # scenarios against real Groq + Jev: after a prompt change
uv run ruff check .
./scripts/check_imports.sh
```

---

## 9. When the structure should grow

| Add | When |
|---|---|
| `voice/` | You start voice (04) |
| `tools/webhook.py` + a `custom_tools` table | Customers want their own actions |
| `adapters/llm/anthropic.py` (or another) | A second model provider |
| Postgres (`asyncpg`) + `docker-compose.yml` | The production phase: change `DATABASE_URL`, run the migrations (06) |
| `evals/` (runner, grader, cassettes) reading `tests/scenarios/**/*.yaml` | The production phase (06) |
| `api/limits.py` (rate limit, token budget) + JSON logging | Before a real public site (06) |
| `runtime/history.py` (`window()`) | Real conversations get long (06) |
| `adapters/decider/llm.py` (`LlmDecider`) | A Jev fallback or a decider comparison (06) |
| `services/insights.py` + `heydesk worker` | Jev conversation labels for the dashboard (06) |
| `ports/cache.py` + `adapters/cache/` | The first cache layer in 06 is built |
| `ports/jobs.py` + a `jobs` table + `heydesk worker` draining it | Something must survive a restart: notifications, reminders (06) |
| `adapters/llm/fallback.py` (`FallbackLLM`) | Groq outages or 429s show up in traces (06) |
| Redis | More than one app process: shared rate limits and caches (06) |
| `ports/notifier.py` + `adapters/notify/` | New-order / new-request notifications, sent through the job lane |
| `ports/payments.py` | Paid orders or deposits |
| `services/billing.py` | You charge by usage (`usage_events` already exists) |
| `models/member.py` (roles, invites) | A workspace has several staff |
| `runtime/knowledge_search.py` | A customer's knowledge outgrows the prompt |
