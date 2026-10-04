# HeyDesk — design

How the MVP works. One page per idea, no more. [PLAN.md](../PLAN.md) says what
to build when; this file says how. Decisions are in [decisions.md](decisions.md).

**Keep it simple.** Build the normal path well. An edge case gets code only
when it's cheap or it would break the demo; otherwise it goes in §12 *Left out*.

---

## 1. What it is

A business picks a **preset** (restaurant host, receptionist), fills in its
details (menu, hours, FAQ) and adds its own notes, and it has an AI
front-desk agent. Visitors talk to it by **web chat** or **voice** in the
browser. It answers questions, takes orders and messages, and hands over to a
person when needed. The business sees orders and messages through the API.

**The model talks; code guarantees.** The model writes the words. Python
decides prices, totals, times, what to ask next and when something may be
saved. Nothing is saved without reading it back and hearing a yes.

## 2. Words

| Word | Meaning |
|---|---|
| Preset | A kind of agent: YAML (greeting, instructions, tools, tasks) + a knowledge model |
| Agent | A business's agent: one `AgentConfig` the business edits. Edits are live on the next message |
| Item | Something a business sells: a dish, a service, a product. One shape for every preset (D17) |
| Conversation | One visitor's chat or call: its `messages` are the whole transcript |
| Turn | One visitor message and everything the agent did for it |
| Tool | Something the model can call: `update_order`, `place_order`… |
| Task | Something a visitor gets done (an order, a message): a list of **details** to collect |
| State note | The end of the system prompt, rebuilt before every model call: what's known, what's next |
| Decider | Jev (TypeSafe): fast yes/no judgments. Used for the commit check |

## 3. The rules

1. **Python does facts.** Prices, totals, dates, times, availability: decided in
   tools, never by the model.
2. **Read back, then a yes, then save.** Writing tools run the commit check
   (§7). It fails closed: if Jev is down, nothing is saved.
3. **Platform rules come first** in the prompt and aren't editable.
4. **The system prompt is built for every turn** from the agent's current
   config, with the channel, date and time at the end. No prompt caching (D15).
5. **Messages are only ever added**, never edited: each turn appends to the
   conversation's `messages`.
6. **`workspace_id` on every table and every query.**
7. **Every turn has a trace**, failures included. A failure is a polite line and
   a hand-off, never silence or a stack trace.
8. **`runtime/` imports no framework, database or SDK.** Only `wiring.py` imports adapters.
9. **Bounded:** at most 6 model calls per turn, a timeout on every tool.

## 4. Layout

```
src/heydesk/
├── main.py  config.py  db.py  errors.py  logs.py  wiring.py  cli.py
├── domain/values.py     Confirmed[T] (only the commit check makes one), NewOrder
├── ports/               Protocols: LanguageModel, Decider
├── adapters/            GigalogyLLM (default), GroqLLM, JevDecider, and fakes for tests
├── runtime/             the engine: loop, tool contract, items, config, prompt, tasks, state note, speech, trace
├── tools/               the tools; DB access only through ctx.services
├── presets/             restaurant/, receptionist/: preset.yaml + knowledge.py
│                        (a business's items are in the `items` table, D17)
├── models/              tables
├── services/            the only code that opens DB sessions
├── api/                 routes: read the request, call one service, return
├── voice/               the voice channel (M2)
└── web/static/          the dev chat page
```

Imports point inwards: `api`/`voice` → `services` → `runtime` → `ports`.
`scripts/check_imports.sh` checks it.

## 5. Agents and presets

`AgentConfig` (`runtime/config.py`) is the agent's settings: name, business
name, timezone, currency, greeting, knowledge, FAQ, hand-off message and phone,
and the owner's **notes**; plus `instructions` (the role text) and `tools` (a
list of names), which come from the preset. One model serves every agent
(`MODEL` in `.env`).

**Owners never meet a technical setting** (D25). An owner edits their business:
greeting, knowledge, FAQ, items, hand-off, and `notes` (tone and rules in their
own words, put in the prompt right after the role text). `instructions` and
`tools` are an admin's: an owner who sends them gets a 403.

A preset (`presets/<id>/preset.yaml`) gives the defaults and the **tasks**:

```yaml
tasks:
  order:
    label: pickup order
    start_when: the visitor wants to order food
    details:                       # asked in this order; the visitor may give them in any order
      - {name: items,       type: cart}
      - {name: pickup_time, type: time_today,  ask: "What time would you like to pick it up?"}
      - {name: name,        type: person_name, ask: "What name should I put the order under?"}
      - {name: phone,       type: phone,       ask: "What's the best number to reach you?"}
    finish_with: place_order
```

`knowledge.py` holds the preset's pydantic knowledge model and `render()`,
which writes the knowledge as short prompt lines.

**Items** (D17). What a business sells is its **items**, in the `items` table,
not in the config. Every preset uses one shape, `runtime/items.py`'s `Item`:
`code` (the short id the model uses, `chk-biryani`), name, description, price,
category, tags, available. So the tools that use them work for any business.
The preset only names them for the prompt (`items_label: Menu`).

- Up to `MAX_ITEMS_IN_PROMPT` (60) items: all listed in the knowledge, one line each.
- More: the prompt gives the count and the categories, and the agent gets
  `search_items` to find them.

**One agent per business** (D22). `agents.workspace_id` is unique, so a
business's agent is just "the agent": no ids in the URLs, no list. Creating a
second is a 409.

**`AgentService`** (`services/agents.py`): `create_from_preset`, `update`
(validates; live from the next message), `get`, `by_key` (the config plus its
rendered knowledge and items). There is no publish step and no versions (D15).
**`ItemService`** (`services/items.py`) saves an agent's items and gives its
tools a `Catalog`: `get(codes)` and `search(query)`, always scoped to one
workspace and agent.

**Dev agent.** In development, startup runs `ensure_dev_agent()`: the `dev`
workspace and the agent `dev-<preset>` from `DEV_AGENT_PRESET` +
`DEV_AGENT_SEED`, and its `items:`. Edit the seed and restart, and the agent
and its items are updated.

## 6. One turn

```
visitor message ─► start_turn: load the conversation (or start one), build the system prompt
                 ─► run_turn: model ⇄ tools, at most 6 model calls
                       text  ─► streamed to the visitor
                       tool  ─► validate ─► run with a timeout ─► result back to the model
                 ─► append the turn's messages, log the trace
```

`ConversationService.start_turn` is the one entry point for web and voice. The
listener (the SSE response, or the voice pipeline) reads the turn's events;
the turn is saved when it ends.

**What the model sees on every call**, in this order:

1. System prompt: platform rules → role text → owner's notes → tasks → knowledge →
   FAQ → **right now** (channel style, date and time, the next 14 days) → the
   **state note** (day 6), rebuilt before every model call and never stored
2. Tool specs: the agent's enabled tools
3. The conversation: the **greeting** first (D8), then every message so far

**Providers** (`LLM_PROVIDER`, D14). Every adapter keeps the transcript in the
OpenAI chat format, so a stored conversation works with any of them.

| | Gigalogy (default) | Groq |
|---|---|---|
| Request | one `query`: system prompt, the tools, then the conversation as labelled lines | system message, tool specs, messages |
| Model | set on the Maira profile (`GIGALOGY_PROFILE_ID`) | `MODEL` (`openai/gpt-oss-120b`), `reasoning_effort` low |
| Streaming | no: the reply arrives whole | yes; tool calls joined at the end |
| Tool calls | **as JSON in the reply** (D16): `{"tool": …, "arguments": …}`, read by the adapter | native |

**Gigalogy tool calls** (D16). The query's `# Tools` section lists each tool
with its JSON Schema and asks for only `{"tool": "<name>", "arguments": {…}}`
to use one, one per reply. Past calls appear in the conversation in that same
form, so the model copies it. The reply arrives whole, so the adapter reads it
before the visitor sees anything: the first `{"tool": …}` becomes a `ToolCall`
(words around it are dropped; JSON it can't read becomes a call with no
arguments, and the tool's validation says what's missing). The runtime can't
tell Gigalogy from Groq.

**The tool loop.** A step is one model call. If it asks for tools, they run
and every call gets its result, then the next step goes to the model. A step
with no tool calls is the reply. At most 6 model calls; still calling tools
after that, or a step with neither words nor tools, is a hand-off.

```
# Conversation state
Task: pickup order
Have: items = 2 × Chicken Biryani, 1 × Borhani, total ৳1,030; pickup time = today, 7:30 pm
Still needed: name, phone
Next: ask for the name. Suggested: "What name should I put the order under?"
```

The note is empty until a task starts: `set_details` names the task, or a cart
starts the task that collects one. When everything is there, **Still needed**
is `nothing` and **Next** says to read it all back and get a clear yes before
the task's `finish_with` tool.

## 7. Tools

The contract lives in `runtime/tool.py` (the engine may not import `tools/`);
the tools themselves are in `tools/`, registered in `TOOLS`. Tools are shared:
one `update_order` serves every business, reaching its items through
`ctx.items`. An agent's config lists the tools it uses; the conversation
service passes the built ones to the turn.

```python
@dataclass(frozen=True)
class ToolContext:
    agent: AgentConfig
    state: dict                    # the conversation state so far
    items: Catalog                 # this agent's items: get(codes), search(query)
    tasks: dict[str, Task]         # the preset's tasks
    now: datetime                  # when the turn started, UTC

@dataclass(frozen=True)
class Tool:
    name: str
    description: str               # for the model: when to use it, what it returns
    input_model: type[BaseModel]   # becomes the JSON Schema the model sees
    run: Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]
    label: str = ""                # web status line while it runs: "Checking the menu…"
    filler: str = ""               # voice line at the start: "Let me check that."
    timeout_s: float = 5.0

@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict                     # short JSON the model reads
    state: dict | None = None      # changes to the conversation state (the cart, details)
    handoff: bool = False          # handoff_to_human: the reply, then the hand-off card
```

`run_tool` validates the arguments, runs the tool under its timeout and turns
any problem into `ok=False` with a hint (`{"error": "…"}`), so the model can
recover. A tool's `state` changes are merged into the conversation's `state`,
saved with the turn.

| Tool | Writes? | What it does |
|---|---|---|
| `update_order` | no | Sets the whole order (item codes + quantities). Checks the items exist and are available; Python computes the total. The cart goes in `state["cart"]` |
| `search_items` | no | Finds items by words in their name, category, description or tags (up to 10). Offered only when the items aren't all in the prompt |
| `set_details` | no | Saves details the visitor gave (`{"pickup_time": "7:30 pm"}`), parsed by their type. Good ones are saved even when another fails; the problems come back as data |
| `place_order` | **yes** | Takes no input: reads the order from state, runs the commit check, saves, returns the order number |
| `submit_request` | **yes** | Same for a message to the team (receptionist) |
| `handoff_to_human` | no | Marks the conversation `needs_human`, returns the hand-off message and phone |

**The commit check** (`runtime/guards.py`): one Jev call with two yes/no
questions: *did the agent's last message read these details back?* and *is the
visitor's reply a clear yes?* Both likely → a `Confirmed[T]`; anything else,
including Jev being down → not confirmed, and the tool tells the model to read
back and ask. "Likely" is P(yes) ≥ 0.7, so "hmm, maybe" fails. Each check logs
its two probabilities (never the text). `OrderService.place()` accepts only
`Confirmed[...]`, so mypy catches a write that skipped the check, and
`scripts/check_imports.sh` checks that only `runtime/guards.py` makes one.
`place_order` reads the order from the state, asks `next_step` first (a missing
detail is asked for, with no Jev call), and once placed clears the task.

**Tasks.** `runtime/tasks.py` has the detail types (`cart`, `text`,
`person_name`, `phone`, `time_today`), each with a `parse` and a `display`
(spoken by `speakable()` on voice, D20). A bare "7:30" is the next 7:30 from now, and a time
that has passed is refused. `next_step(task, filled)` returns the first missing
required detail, or None: everything's there, so read it back. The state note and `place_order` both use
it, so "what do we ask next?" and "can we save?" can't disagree.

## 8. Data model

Every table has `workspace_id` (except `workspaces`).

| Table | Columns |
|---|---|
| `workspaces` | id, name, status (`active`/`suspended`) |
| `users` | workspace_id (empty for an admin), role (`owner`/`admin`), email, password_hash |
| `audit_events` | actor_id, workspace_id, action, target, at: what an admin did in a business. Insert-only |
| `agents` | preset, name, public_key (unique), config JSON |
| `items` | agent_id, code (unique per agent), name, description, price, category, tags JSON, available, position |
| `conversations` | agent_id, channel, status (`active`/`needs_human`), state JSON, messages JSON |
| `records` | agent_id, conversation_id, job (the task), kind (`order`/`request`), number (101, 102… per business and kind), status, customer_name, customer_phone, details JSON, note; an order's lines JSON, total, currency, due_at (the pickup time) (D24) |

Money is in minor units (paisa). Datetimes are stored in UTC; "today" is the
business's timezone. JSON columns: assign a new value to change one, because
SQLAlchemy doesn't see in-place edits.

## 9. API and streaming

| Method | Path | Does |
|---|---|---|
| GET | `/` | Dev chat page (development only) |
| GET | `/api/chat/{key}` | Agent name and greeting |
| POST | `/api/chat/{key}` | `{message, conversation_id?}` → Server-Sent Events |
| POST | `/api/auth/signup`, `/api/auth/login` | A new business and its owner, or a login → `{token}` |
| GET | `/api/auth/me` | Who you are: email, role, your business |
| GET/PATCH/POST | `/api/admin/…` | Admins only (404 for owners): businesses (suspend), users (reset a password), the audit log |
| GET | `/api/presets` | Presets and their knowledge JSON Schema |
| GET/POST/PATCH | `/api/agent` | The business's one agent: create from a preset, read, edit; problems per field |
| GET/PUT | `/api/agent/items` | The agent's items, replaced whole |
| GET | `/api/inbox` | The jobs the agent does, each with how many records wait |
| GET/PATCH | `/api/records…` | Orders and messages, newest first (`?job=&status=&q=`); one record; move its status |
| GET | `/api/conversations…` | Conversations (`?status=needs_human`), and one with what was said |
| GET | `/health` | DB reachable |

Everything under `/api` except chat, presets and auth needs
`Authorization: Bearer <token>`.

**Two roles** (D23). An **owner** belongs to one business: the token says which
(rule 6), and they only ever see it. An **admin** runs the platform and belongs to
none: made only by `uv run heydesk create-admin EMAIL PASSWORD`, never by signup.
An admin works inside a business by sending its id in an `X-Business` header on
the ordinary routes (an owner's header is ignored). The database, not the token,
says someone is still an admin. Every admin write, and every admin read of one
thing (one conversation), is saved to `audit_events`. A suspended business's chat
answers 404 `unavailable`; its owner can still log in.
`uv run heydesk create-user EMAIL PASSWORD` adds an owner to the dev workspace.

Every error is `{"error": {"code", "message"}}`, with `fields` (path → problem)
for a 422. A conversation takes at most `MAX_TURNS` visitor messages (D21). A status
change a record doesn't allow is a 409 `invalid_transition` (an order: new →
ready → collected, or cancelled; a message: new → handled → archived).

SSE events: `open {conversation_id}`, `token {text}`, `status {label}`,
`handoff {message, phone}`, `done {interrupted}`, `error {message}`.

Every turn logs one JSON line, `turn finished`, with its trace: steps, timings,
tokens, cost, error (rule 7). There is no trace table (D15).

## 10. Voice (M2)

A `voice/` package turns speech into `start_turn(channel="voice")` calls and
the turn's events back into speech, with Pipecat (D18): the browser's audio over
WebRTC (`POST /api/voice/offer`), Silero VAD, Chorus for speech-to-text (one
upload per utterance, after 0.5s of silence) and Piper for text-to-speech until
the real one is added.

- **Sentences:** the reply's tokens go straight to Pipecat's TTS, which speaks them
  sentence by sentence, so speech starts after the first sentence.
- **Spoken forms:** the model always sees display forms ("৳450", "7:30 pm"). On
  voice, each sentence goes through `runtime/speech.py`'s `speakable()` just
  before the TTS, which says money, times, phone numbers and order numbers in
  words ("four hundred and fifty taka") (D20). The "right now" section asks for
  short, list-free replies.
- **Fillers:** when a tool with a `filler` starts, the voice channel says it.
  Fillers never enter the transcript, and the platform rules tell the model
  never to say "let me check" itself.
- **Barge-in:** when the caller talks over the agent, the framework stops the
  audio and the channel sets the turn's interrupt flag; the turn stops at the
  next token and is saved as `interrupted`. Talking over it for 0.6s stops it; a
  short "mm-hmm" doesn't, and isn't a turn. The old turn is saved before the next
  one starts.

## 11. Testing

- Unit tests for `runtime/`, `tools/` and `domain/` with `FakeLLM`,
  `FakeDecider` and the fixed clock (`FIXED_NOW` = Tue 29 Sep 2026 08:00 UTC =
  14:00 in Dhaka).
- Integration tests on in-memory SQLite through the `world` and `client`
  fixtures (`tests/conftest.py`).
- A few whole conversations scripted with `FakeLLM`: an order placed only after
  a yes, a message taken, a hand-off.
- Live runs against Groq and Jev only with `-m live`, and only when asked: they cost quota.

## 12. Left out on purpose

Each of these is real, but not worth the code for the MVP. The production
version handles them.

| Left out | What happens instead |
|---|---|
| Turn keeps running after a closed tab | The turn runs inside the listener; closing the tab mid-reply loses that turn |
| Retry replay (`client_message_id`), idempotency keys | A retried message is a new message |
| One reply at a time per conversation (a lease) | The page blocks sending while a reply streams; two at once would both run |
| Agent versions, a publish step, edit history | Edits are live on the next message, even mid-conversation |
| Prompt caching | The prompt is rebuilt every turn; it costs a little more |
| Hand-off guard on every turn | Hand-off is a tool the model calls |
| Ranked or fuzzy item search | `search_items` matches words (`LIKE`), at most 10, in the business's order |
| Loading only the items a turn needs | Every turn reads all of an agent's items to build the prompt; fine for hundreds |
| Several tool calls in one Gigalogy reply | The first `{"tool": …}` runs; the model sees its result and can call the next |
| Progress-line policy (thresholds, rotation) | Web shows the tool's label; voice says the tool's filler |
| What the caller *heard* after a barge-in | The turn's trace says `interrupted`; the text is what was generated |
| Usage events, budgets, rate limits | Cost is in each turn's log line |
| Bookings, the clinic preset, custom tasks | Restaurant + receptionist |
| Teams, roles, billing, the Vue app, phone numbers, deploy | Swagger and the dev page are the UI; the demo runs locally |
