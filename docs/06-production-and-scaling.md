# 06 — From MVP to production: the upgrade path

The first month builds an **MVP to show your mentor**, not a production
system. This doc says how it becomes one without a rewrite:

- what the MVP builds now because it would be expensive to add later (§2);
- what's deferred, where each item plugs in, and how big the change is (§3);
- the advanced features from Frontdesk (caching, fallbacks, the durable job
  lane, scaling out), for after that (§4–§7).

Read with: [01-frontdesk-analysis.md](01-frontdesk-analysis.md),
[02-build-guide.md](02-build-guide.md), [03-project-structure.md](03-project-structure.md).

---

## 1. HeyDesk next to Frontdesk

**Today HeyDesk is a design and Frontdesk is working code**: about 7,500 lines
in the package, 343 tests, 38 graded scenarios with cassettes. So HeyDesk
isn't production grade yet; nothing is, before it runs. Frontdesk isn't
production grade either. It's a single-business demo: SQLite, one process, an
in-memory cache, a fake clinic, stub payments, admin reset endpoints, and no
login.

The useful question is which one is the better **base** for the product you
want. Compared part by part:

| | Frontdesk (built) | HeyDesk (designed) | Better base |
|---|---|---|---|
| Kinds of business | One clinic; a new business is a new pack **in code** | Presets + **custom tasks as data**; a new business is mostly a form | HeyDesk |
| Conversation control | A state machine in code: very predictable, not editable | Tasks + `next_step` + the state note + tools: predictable where it matters, editable | HeyDesk for a SaaS; it depends on evals to stay predictable |
| Guarantees on writes | `Confirmed[T]`, three DB constraints, idempotency | The same, plus the Jev commit check on the same read-back text | Equal |
| Many customers | None | Workspaces, auth, isolation tests, agent versions | HeyDesk |
| Database | SQLite, raw SQL | Postgres, SQLAlchemy async, Alembic | HeyDesk |
| Tests and evals | Built, with record/replay | Scenario tests now; record/replay later | Frontdesk until built |
| Caching and speed | Tiered tool cache, in-flight joins, prefetch, audio cache | Provider prompt cache only, at first | Frontdesk (§4 is the plan) |
| Background work | Durable queue: leases, backoff, reconciliation | None in the MVP | Frontdesk (§6 is the plan) |
| Payments, voice | Built (stub payments, simulated voice) | Later (04) | Frontdesk |
| Cost and abuse limits | Not needed for a demo | Turn cap now; rate limits and a token budget later | HeyDesk, once added |

**Structure.** HeyDesk keeps what made Frontdesk's code good:
- a runtime with no framework, database or provider SDK in it;
- ports for every provider, with fakes;
- one composition root (`wiring.py`);
- import rules checked by a script;
- a trace for every turn, including the ones that fail.

It deliberately drops Frontdesk's pure aggregates and in-memory repositories.
For a CRUD-heavy SaaS they're a mapping layer that pays for itself less often.

**What changed after this comparison** (now in 02 and 03):

1. **`Confirmed[T]` is back** (`domain/values.py`). The services accept only a
   confirmed value, and only the commit check makes one.
2. **`ports/stores.py`**. Tools depended on the services' type, which broke
   the rule that tools never import services. They now depend on Protocols
   the services implement.
3. **Custom tasks + `submit_request`**. Any business can collect structured
   details ending in a request to its inbox, without code. This is what lets
   HeyDesk serve businesses no preset foresaw.
4. **Scenarios as data.** Scenario tests are YAML files, so Frontdesk's eval
   runner with cassettes can be added later and read the same files.
5. **`Tool.role`** in place of `commits: bool`. It costs nothing now, and the
   cache rule reads it later.

---

## 2. Built now, because it's expensive to add later

These are in the MVP even though a demo could skip them. Each one would
otherwise mean touching most of the code, or data you can't get back.

| Built in the MVP | Why it can't wait |
|---|---|
| `workspace_id` on every row and query, isolation tests | Adding tenancy later means changing every query and every table |
| Agent versions pinned to conversations | Without them, an old conversation has no record of the prompt that produced it |
| Append-only transcripts, a trace for every turn | History you didn't record can't be recreated |
| `usage_events` for every turn | Budgets, usage reports and billing later just sum it |
| Commit check → `Confirmed[T]`; DB constraints; idempotency keys | Correctness; a double order is a real business's real problem |
| Ports + fakes + `wiring.py` | Every provider upgrade (fallback, cassettes, a comparison) becomes one adapter |
| Alembic from day one, portable SQL, `UTCDateTime` | Postgres becomes a `DATABASE_URL` change |
| The turn lease + the conversation's `version` column (one reply at a time) | Already safe for several app processes; concurrency bugs found after launch cost real orders |
| Turns that run in their own task, interruptible by a signal (02 §10) | A disconnect or a barge-in can't lose a turn that already wrote; graceful shutdown is "await the running turns" |
| `client_message_id` on every message | Retries from flaky networks and proxies are safe from the first user |
| JSON logs with ids, mypy, CI (from M0) | Log context and type discipline are painful to retrofit into a codebase that grew without them |
| The progress policy and spoken forms (07) | Voice and every slow integration later reuse them; they're pure code with tests |
| `Tool.role` | The cache rule "never cache a commit" has something to read |
| Scenario tests as YAML | The eval runner later reads the same files |
| Timeouts on provider clients | Without them a hung provider hangs a visitor's chat |

---

## 3. The production phase

In a sensible order. **Size** is the change to existing code, not the new
code: *small* means a new file or two and a line in `wiring.py`, `main.py` or
`start_turn`.

| # | Upgrade | Plugs in at | Size |
|---|---|---|---|
| 1 | **Postgres**: `asyncpg`, `docker-compose.yml`, managed Postgres with backups | `DATABASE_URL`; run the same migrations; run the tests on Postgres | Small |
| 2 | **Rate limit** per IP and per conversation on `POST /api/chat` | `api/limits.py`, a FastAPI dependency; in memory, Redis when there's more than one process | Small |
| 3 | **Daily token budget** per workspace | A sum over `usage_events` and an `if` at the top of `start_turn`; over budget → the hand-off message, no model call | Small |
| 4 | **Observability**: error tracking; `/ready`; log shipping and dashboards on `ttft_ms`, `first_output_ms` and hand-off rate. (JSON logs with `workspace_id`, `conversation_id` and `turn` are in from M0: ids only, never message text or phone numbers) | `main.py`; one route | Small |
| 5 | **Deploy hardening**: migrations before the new version starts; graceful shutdown that waits for running turns (the turn runner's task set) and lets streams finish; secrets | Deploy scripts, the lifespan | Small |
| 6 | **Adapter resilience**: one retry before the first token; `FallbackLLM` (§5) | Inside `adapters/llm/`; one line in `wiring.py` | Small |
| 7 | **Eval harness**: `evals/runner.py` (`--live`, `--record`, `--replay`), `grader.py`, cassettes | Reads `tests/scenarios/**/*.yaml`; record/replay are wrapper adapters around `LanguageModel` and `Decider` | Medium (new code, no changes) |
| 8 | **History budget**: `runtime/history.py` (`window()`) | The one marked line in `build_request` (02 §7) | Small |
| 9 | **Background worker + Jev labels**: `heydesk worker`, `services/insights.py`, `LlmDecider` | New files; the `labels` column already exists | Medium (new code, no changes) |
| 10 | **Builder extras**: preview chat on a draft, version history, embeddable widget with per-agent allowed domains | Services and API already have versions; mostly Vue | Medium |
| 11 | **Privacy**: a retention setting per workspace, delete-a-conversation | A service method and a periodic job | Small |

Nothing in this table changes the runtime, the tools or the data model's
shape. That's the test of the MVP's structure.

---

## 4. Caching and speed

Three rules, all from Frontdesk:

- **A write is never served from a cache.** `cacheable(tool)` is
  `tool.role != "commit" and tool.ttl_seconds > 0`: a rule in code, not a
  setting a preset can get wrong.
- **Every hit and miss shows up in the trace.** A cache you can't see working
  is a guess.
- **Measure first.** Each layer below has a trigger. Build it when the traces
  show the problem, not before.

And one rule of HeyDesk's own:

- **Every cache key starts with `workspace_id`** (and the agent version where
  it matters). One customer's cached answer must never reach another's visitor.

| # | Layer | Saves | Plugs in at | Build when |
|---|---|---|---|---|
| 1 | **Provider prompt cache** (automatic on Groq's gpt-oss) | Half the input cost of the stable prefix, and time to first token | The adapter; rules 4 and 5 keep the prefix stable | **Now** |
| 2 | **Published-version cache** | Loading the version and its prompt on every turn | `AgentService.published_by_key()`: version by id never changes, so it never goes stale; the key → version-id lookup lives ~30 s or is cleared on publish | A profile shows the load in time to first token |
| 3 | **Tool read cache, tiered by role** | Repeated slow reads: availability now; external calendars and webhook tools later | `run_tool()` through `ports/cache.py`; `search` tools get seconds, `read` tools minutes | The first read that calls another system, or availability shows up slow in traces |
| 4 | **In-flight joins** | Two identical reads at the same moment | Beside layer 3: a dict of running reads by cache key; the second waits for the first | With layer 3, or with prefetch |
| 5 | **Speculative prefetch** | The lookup's wait, on voice | `voice/`: start a read from a half-finished sentence into layer 3 (04) | Voice |
| 6 | **Rendered-audio cache** | Speaking common phrases again | `voice/` (04) | Voice |
| 7 | **Shared cache (Redis)** | Layers 2–4 across processes | A second `ports/cache.py` adapter | More than one app process (§7) |

**Not on the list: caching answers.** Frontdesk could skip the model for a
known FAQ because its replies were one sentence chosen by code. In HeyDesk an
answer depends on the conversation, the time ("are you open now?") and the
visitor's order, so a cached answer is often a wrong one. The prompt cache
already makes these questions cheap. If you ever add it, limit it to the first
message of a conversation, key it by agent version + normalised question, and
never use it for anything that depends on the time.

---

## 5. Providers: fallback and comparison

- **`FallbackLLM`** (`adapters/llm/fallback.py`). Primary: Groq
  `gpt-oss-120b`. Secondary: another host serving the same open-weight model
  through an OpenAI-compatible API. It switches only on `LLMUnavailable`
  **before the first token**. Transcripts are stored in the OpenAI chat
  format, so the conversation carries on. The trace records which host
  answered. The first call on the secondary misses its cache; that's fine.
- **Circuit breaker**, inside `FallbackLLM`: after a few failures in a minute,
  go straight to the secondary for a minute instead of paying the timeout on
  every turn.
- **Decider comparison.** Frontdesk's `ComparingDecider` runs a shadow decider
  beside the real one and logs where they disagree. Use it to tune the 0.7
  commit thresholds from real traffic, and to check a new Jev version before
  switching `TYPESAFE_MODEL`.

---

## 6. Background work

**MVP:** nothing runs in the background.

**Production phase:** `heydesk worker`, a separate process running periodic
tasks: Jev labels for quiet conversations, closing idle conversations.

**Later, the durable lane** (Frontdesk's `core/jobs.py`, on Postgres):
- a `jobs` table;
- workers claim jobs with `SELECT … FOR UPDATE SKIP LOCKED` and hold a lease;
- failed jobs retry with backoff, and jobs that keep failing are marked dead;
- a reconciliation sweep catches anything missed.

Enqueue a job **in the same transaction** as the row it's about (the order,
the request). A crash can't then leave an order with no notification, or a
notification with no order.

First users of the lane:
- new-order and new-request notifications (email, SMS or WhatsApp through
  `ports/notifier.py`);
- booking reminders;
- retries for custom webhook tools.

---

## 7. Scaling out

One async process handles many chats at once, because most of a turn is
waiting on Groq. Measure before adding processes. When you do:

- **Nothing that must be shared lives in process memory.** Rate-limit
  counters and caches move to Redis (a second adapter for each). The
  one-reply-at-a-time lock is already in Postgres (the turn lease and the
  conversation's `version`).
- **Voice workers** (M4's `voice/`) move to their own processes when calls
  need their own scaling. They only call `start_turn()`, so they scale like
  any other listener.
- **Database connections:** never hold a transaction open while waiting on a
  model (already a rule in 02 §8), so a small pool serves many streams.
  (Postgres first: SQLite allows one writer at a time.)
- **Streaming through a proxy:** buffering off (`X-Accel-Buffering: no`
  already), and proxy read timeouts longer than the longest reply.
- **Workers scale separately** from the app: `heydesk worker` processes can be
  added without touching the web tier, because `SKIP LOCKED` lets them share
  one queue.
