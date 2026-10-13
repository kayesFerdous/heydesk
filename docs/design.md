# HeyDesk — design

How the MVP works. One page per idea, no more. [PLAN.md](../PLAN.md) says what
to build when; this file says how. Decisions are in [decisions.md](decisions.md).

**Keep it simple.** Build the normal path well. An edge case gets code only
when it's cheap or it would break the demo; otherwise it goes in §12 *Left out*.

---

## 1. What it is

A business picks its **kind** (a restaurant, a clinic, a receptionist for an office), fills in
its details (address, hours, its menu or doctors, FAQ) and adds its own notes, and it has an AI
front-desk agent. Each kind is a **desk** with its own prompt, tools and tables (D41). Visitors talk to it by **web chat** or **voice** in the
browser, or on **Telegram** with the business's own bot (D42). It answers questions, takes orders and messages, and hands over to a
person when needed. The business sees orders, appointments and messages through the API.

**The model talks; code guarantees.** The model writes the words. Python
decides prices, totals, times, what to ask next and when something may be
saved. Nothing is saved without reading it back and hearing a yes.

## 2. Words

| Word | Meaning |
|---|---|
| Desk | A kind of business (`restaurant`, `clinic`, `receptionist`): one folder in `desks/` with its own config fields, prompt, tools, tables and owner routes (D41) |
| Agent | A business's agent: one desk's config, which the business edits. Edits are live on the next message |
| Store | A business's data as its desk sees it (`ClinicStore`: doctors, appointments, messages), always scoped to one workspace |
| Conversation | One visitor's chat or call: its `messages` are the whole transcript |
| Turn | One visitor message and everything the agent did for it |
| Channel | Where a visitor talks: `web`, `voice` or a messaging app (`telegram`). A messaging app's link to the business is a row in `channels` (D42) |
| Tool | Something the model can call: `update_order`, `request_appointment`… A desk's own, plus the shared `take_message`, `handoff_to_human`, `off_topic` and `search_knowledge` |
| Note | A desk's line from the conversation state (the restaurant's order so far), added to the system prompt before every model call |
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
├── domain/values.py     Confirmed[T] (only the commit check makes one), record codes ("A-104")
├── ports/               Protocols: LanguageModel, Decider, Embedder, KnowledgeStore
├── adapters/            GigalogyLLM (default), GroqLLM, JevDecider, Gemini, Qdrant, and fakes
├── runtime/             the engine: loop, tool contract, commit check, parsers, prompt, speech
├── desks/               one folder per kind of business (D41)
│   ├── base.py          Desk (what a desk is) and Store (its data: messages, documents search, its list replaced whole)
│   ├── shared.py        take_message, handoff_to_human, off_topic, search_knowledge
│   ├── restaurant/      __init__ (config, prompt) · tools · values · models · store · api
│   ├── clinic/          the same
│   ├── shop/            the same
│   ├── receptionist/    __init__ only: no tools or tables of its own
│   └── csv_import.py    reading a CSV file into a desk's list; matching its columns to ours (D50, D53)
├── models/              the shared tables; `taken.py` is what orders, appointments and messages share
├── services/            agents, conversations, visitors (the limits, D51), users, admin, knowledge, imports (D53)
├── api/                 routes: read the request, call one service or store, return
├── voice/               the voice channel (M2)
├── adapters/telegram/   the Bot API client (and its fake); the channel is `services/channels.py` (D42)
└── web/static/          the dev chat page
```

Imports point inwards: `api`/`voice` → `services` → `desks` → `runtime` → `ports`. A desk's
tools reach the database only through its store, and only its `api.py` knows HTTP.
`scripts/check_imports.sh` checks it.

## 5. Agents and desks

**A desk is a kind of business, written as plain Python** (D41). Each folder in `desks/` has:

| File | What's in it |
|---|---|
| `__init__.py` | The desk: its config class, its `ROLE` text, `prompt()` (its sections of the system prompt, with its facts), `note()`, `missing()` and `seed()` |
| `tools.py` | Its tools: the restaurant's `update_order` and `place_order`; the clinic's `find_doctor`, `request_appointment`, `cancel_appointment`, `reschedule_appointment`; the shop's `update_cart`, `place_order`, `track_order`, `change_order`, `cancel_order`, `request_exchange` |
| `values.py` | Its shapes, no database: `MenuItem`, `NewOrder`; `Doctor` (with `works_at`), `NewAppointment`; `Product`, `Zone`, `ShopConfig`, `mobile()` |
| `models.py` | Its tables: `menu_items` and `orders`; `doctors` and `appointments`; `products` and `shop_orders` |
| `store.py` | Its data access, for its tools and its owner routes, always one workspace |
| `api.py` | Its owner routes: `/api/menu`, `/api/orders`; `/api/doctors`, `/api/appointments`; `/api/products`, `/api/shop/orders` |

`desks/__init__.py` lists them (`DESKS`), and `tools_for(desk, has_documents)` is a desk's tools
then the shared ones: `take_message`, `handoff_to_human` and `off_topic` (D51) always,
`search_knowledge` when the business has documents (D35). To add a kind of business: a new folder, and a line in `DESKS`.

**The config.** `runtime/config.py`'s `AgentConfig` has what every business has: name, business
name, timezone, currency, greeting, address, `hours`, the owner's **notes**, FAQ, hand-off. A desk
subclasses it: the clinic adds `emergency_phone` and `policies`, the receptionist `about` and
`services`, the shop `delivery` (its areas, each with a charge, usual days and the `places` buyers name in it, D54), `free_delivery_over`, `policies`, `size_guide` (D54) and `exchange_days` (D55); the restaurant `delivers` and `delivery_charge` (one charge to any address), the clinic `deposit`; and the restaurant, clinic and shop each `pay_first` (D47, D48), which needs something to take (a 422 otherwise); each sets its own default greeting. A field the desk doesn't have is a 422, so
there's no role text to overwrite: it's the desk's code. Only the fields actually given are saved,
so a default (a greeting) can change later. One model serves every agent (`MODEL` in `.env`).

**Setup is one call and a list** (D26). `POST /api/agent` needs only `{"kind": "restaurant"}`:
the business name comes from signup. `GET /api/agent` then says what's `missing`, in plain words
("Add your address.", "Add your menu."); it's empty when the agent is set up. There's no publish
step (D15): the agent answers from the start, and what's not filled in is simply not in its prompt.

**The menu and the doctors are tables, and they're all in the prompt.** A restaurant's
`menu_items` (code, name, description, price, category, tags, available) and a clinic's `doctors`
(code, name, speciality, fee, `days`, `start`, `end`, languages, description, available) are
replaced whole with `PUT`. The prompt lists them all, one line each; there's no search for a
long list (D41).

**One agent per business** (D22). `agents.workspace_id` is unique and `agents.kind` names its
desk, so a business's agent is just "the agent": no ids in the URLs. Creating a second is a 409.
A desk's routes answer 404 for another kind (a restaurant has no doctors).

**`AgentService`** (`services/agents.py`): `create`, `update` (validates; live from the next
message), `get` (with `missing`), `by_key` (a `LiveAgent`: its desk, config and document titles),
`store(ws, kind)` for the owner routes. There is no publish step and no versions (D15).

**Dev agent.** In development, startup runs `ensure_dev_agent()`: the `dev` workspace and the
agent `dev-<kind>` from `DEV_AGENT_KIND` + `DEV_AGENT_SEED`; the desk's `seed()` takes its own
list (`menu:`, `doctors:`) out of the seed file. Edit the seed and restart, and the agent is updated.

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

**What the model sees on every call**, in this order (each rule and why: [prompt-rules.md](prompt-rules.md)):

1. System prompt: platform rules → the desk's own sections (its role, what it can do, its facts)
   → owner's notes → FAQ → documents → **right now** (channel style, date and time, open or
   closed today, the next 14 days with the closed ones marked, D40) → the desk's **note**
   (the restaurant's order so far), rebuilt before every model call and never stored
2. Tool specs: the desk's tools and the shared ones
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
after that, or a step with neither words nor tools, is a hand-off. A tool can
also end the turn with its own `reply`, said as it is (`off_topic`, D51).
`TurnEnded.tools` lists the tools that ran.

```
# The order so far
2 × Chicken Biryani, 1 × Borhani, total ৳1,030
```

That's the restaurant's note, empty until there's an order. The other desks have none: what's
still needed comes back from the writing tool itself (§7).

## 7. Tools

The contract lives in `runtime/tool.py`; the tools live in their desk (`desks/clinic/tools.py`)
or in `desks/shared.py`. A tool reaches the database only through `ctx.store`, its desk's store.

```python
@dataclass(frozen=True)
class ToolContext[S]:
    agent: AgentConfig             # the desk's own config
    store: S                       # the desk's store: ClinicStore, RestaurantStore
    state: dict                    # the conversation state so far (the cart)
    now: datetime                  # when the turn started, UTC
    decider: Decider               # for the commit check
    agent_said: str                # the agent's last message: the read-back, if there was one
    visitor_said: str              # this turn's message: the yes, if there was one

@dataclass(frozen=True)
class Tool:
    name: str
    description: str               # for the model: when to use it, what it returns
    input_model: type[BaseModel]   # becomes the JSON Schema the model sees
    run: Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]
    label: str = ""                # web status line while it runs: "Checking the menu…"
    filler: str = ""               # voice line at the start: "One moment."
    timeout_s: float = 5.0
```

`run_tool` validates the arguments, runs the tool under its timeout and turns any problem into
`ok=False` with a hint (`{"error": "…"}`), so the model can recover. A tool's `state` changes are
merged into the conversation's `state`, saved with the turn.

**A writing tool takes its details as arguments** (D41). Every one has the same three answers:

1. **Something's missing or wrong**: `{"problems": {...}, "still_needed": [...], "next": "Ask for
   the name…"}`. Each detail is checked by Python as it's taken (`Checks.take`); the model may call
   as soon as it has a part, so a closed hour or a doctor's day off is caught early.
2. **Everything's there, no yes yet**: `{"read_back": "...", "next": "Read this back and ask for a
   clear yes…"}` (`ask_for_yes`).
3. **A call after a clear yes**: saved, with its number or code.

| Tool | Desk | Writes? | What it does |
|---|---|---|---|
| `update_order` | restaurant | no | Sets the whole order (codes and quantities). Checks the dishes exist and are available; Python adds up the total. Refuses on a closed day (pickup is today only, D40). The cart goes in `state["cart"]` |
| `place_order` | restaurant | **yes** | `delivery` (when the restaurant `delivers`), `time`, `address` (for a delivery), `name`, `phone`, `note`. The time must be later today and inside the hours; a delivery adds `delivery_charge` to the total. Returns the order number, and an alert for the owner (D43). With `pay_first`, a delivery order is saved `unpaid` and it returns a `pay_link` for the delivery charge instead, and no alert; pickup pays nothing first (D48) |
| `find_doctor` | clinic | no | Doctors by `speciality`, `day` (today, tomorrow, a weekday) and `language`. Python turns the day into a date and keeps the doctors who work it |
| `request_appointment` | clinic | **yes** | `doctor`, `time`, `name`, `phone`. The doctor is matched by code or name words; the time is a day and a time in the next 14 days, inside the clinic's hours and the doctor's `days`/`start`–`end`. Returns its code, `A-104`; the clinic phones to confirm. With `pay_first`, it saves the request `unpaid` with the clinic's `deposit` and returns a `pay_link` instead (D48). Moving an unpaid one keeps it unpaid |
| `cancel_appointment` | clinic | **yes** | The code (`A-104`, `a104`, `a 104` all work) and the name it's under; a wrong code and a wrong name get the same answer, so nothing leaks (D38) |
| `reschedule_appointment` | clinic | **yes** | The same, plus a new time checked like a new request. A moved appointment is `new` again |
| `take_message` | every desk | **yes** | `message`; optional `name`, `phone`, `best_time` (checked when given; the agent offers once to take them, D44). Returns its code, `M-104`, and an alert for the owner |
| `search_knowledge` | every desk, with documents | no | The best 4 passages from the business's documents, with their titles and a reminder that they are facts, never instructions (D35) |
| `update_cart` | shop | no | Sets the whole order: codes, quantities, and a size and colour when the product has them. Python checks the choices and the stock and adds up the subtotal. Under `free_delivery_over`, it also says how much more makes the delivery free (`free_delivery_if_you_add`, D54). The cart goes in `state["cart"]` (D45) |
| `place_order` | shop | **yes** | `area` (one of the shop's delivery areas, or a place in one: "Mirpur 10" is Inside Dhaka, D54), `address`, `name`, `phone` (a Bangladeshi mobile, Bangla digits and +880 accepted), `note`. Gives the delivery charge and total as soon as the area is known; free over `free_delivery_over`. Takes the items out of stock as it saves. Returns its code, `O-101`, what to say next (the call, the cash for the rider, the usual days), and an alert, which also warns when this mobile has returned or cancelled orders before and lists the order's products at `LOW_STOCK` (3) or fewer (D54). With `pay_first` (and a charge), it saves the order `unpaid` and returns a `pay_link` instead, and no alert: the payment places it (D47) |
| `track_order` | shop | no | No code for the one order placed in this conversation (D55); otherwise the code and the mobile it was placed with: its status in words, items, total, what was paid online and what's left for cash on delivery, how long delivery usually takes, the courier the owner gave, and the `pay_link` while it's unpaid. A wrong code and a wrong number get the same answer |
| `change_order` | shop | **yes** | Found like `track_order`; then any of a new `address`, `area` (or a place in one), `new_phone`, `note`, only while `new` or `confirmed` (`CHANGEABLE`). A new area works out the delivery charge and total again; not once its charge was paid online. The items can't change: cancel and order again. Reads back only what changes; alerts the owner (D55) |
| `cancel_order` | shop | **yes** | Found like `track_order`; only while `unpaid`, `new` or `confirmed` (not once the courier has it). The items go back in stock |
| `request_exchange` | shop | **yes** | Found like `track_order`; only a `delivered` order, within `exchange_days` of being marked delivered. `product` (a code from the order) and the `size` and/or `colour` wanted, checked against the product's own (`Product.choose`, as `update_cart`), optional `reason`. Saved as a message for the team (`M-104`) with an alert: the owner arranges the swap; no stock changes (D55) |
| `handoff_to_human` | every desk | no | Marks the conversation `needs_human` and alerts the owner (D43), returns the hand-off message and phone |
| `off_topic` | every desk | no | Called instead of answering a message that has nothing to do with the business. Its `reply`, one short line in the visitor's language, is sent as it is and ends the turn: no second model call. Each call is a strike for the visitor (D51) |

**The commit check** (`runtime/guards.py`): one Jev call with two yes/no questions: *did the
agent's last message read these details back?* and *is the visitor's reply a clear yes?* Both
likely → a `Confirmed[T]`; anything else, including Jev being down → not confirmed, and the tool
returns its read-back. "Likely" is P(yes) ≥ 0.7, so "hmm, maybe" fails. Each check logs its two
probabilities (never the text). A store's writing methods accept only `Confirmed[...]`, so mypy
catches a write that skipped the check, and `scripts/check_imports.sh` checks that only
`runtime/guards.py` makes one.

**Parsing** (`runtime/parse.py`): `name`, `phone`, `text`, `time_today` (a bare "7:30" is the next
7:30 from now; a time that has passed is refused) and `day_and_time` ("tomorrow at 5 pm",
"saturday 5pm", "5 Oct 6:30 pm"; within 14 days; a time with no am/pm is asked again, never
guessed). Both times must be inside the business's opening hours (D31; seven closed days are
closed all week, D40), and a refusal's hint says the hours.

### The knowledge base (D33–D35)

A business adds **documents** (pasted text, `.txt`, `.md` or `.pdf`) to its agent. The FAQ field stays in the
prompt; documents are for what's too long or too many to fit there.

```
add: text ─► chunks (~1,200 chars, break at paragraphs, then sentences; a little overlap)
          ─► Gemini embeds them in one batch ─► Qdrant (dense + sparse vector per chunk)
          ─► the text is saved in SQL (`documents`), the index can be rebuilt from it
ask: the agent calls search_knowledge(question) ─► Gemini embeds the question
          ─► Qdrant: dense search + sparse (exact words) search, fused (RRF), filtered to this business
          ─► the best 4 passages go back to the model, which answers only from them
```

- **Hybrid on purpose.** Dense search finds the same meaning in other words; the sparse side finds the exact
  price, name or number a vector blurs. Words are cut by Unicode category so Bengali isn't split at its vowel signs.
- **Isolation.** One Qdrant collection, a `workspace_id` payload on every chunk, and every query filters by it.
- **Fails soft.** If the question can't be embedded, search uses words only. If Qdrant is down, the tool returns
  a failure and the agent offers a message or a person. Adding a document is all-or-nothing: if embedding or storing
  fails, nothing is saved.
- **Limits.** 30 documents per business, 300,000 characters (about 100 pages) each, files up to 10 MB.
  Scanned PDFs have no text layer: they're refused with a reason.
- `uv run heydesk reindex-knowledge` rebuilds every chunk from the saved text (a new collection or model).

## 8. Data model

Every table has `workspace_id` (except `workspaces`).

| Table | Columns |
|---|---|
| `workspaces` | id, name, status (`active`/`suspended`) |
| `users` | workspace_id (empty for an admin), role (`owner`/`admin`), email, password_hash |
| `audit_events` | actor_id, workspace_id, action, target, at: what an admin did in a business. Insert-only |
| `agents` | kind (its desk), name, public_key (unique), config JSON |
| `conversations` | agent_id, channel, contact (a messaging app's chat id, D42), status (`active`/`needs_human`/`human`: the team is answering, D43), state JSON, messages JSON |
| `visitors` | key (`web:<the page's token>`, `telegram:<chat id>`; unique per business), day (the business's local day), turns (on that day), strikes (off-topic turns in a row), blocked_until (D51) |
| `channels` | agent_id, kind (`telegram`), token, secret, address (the bot's username), owner_chat_id (where alerts go, D43). One per agent and kind (D42) |
| `payment_accounts` | store_id, password, sandbox: the business's own SSLCommerz store, one per business (D49). No route returns the password |
| `documents` | agent_id, title, filename, kind (`text`/`markdown`/`pdf`), text, chunk_count (D34). Its chunks are in Qdrant, not here |
| `team_messages` | the shared columns, body, best_time. Code `M-104`; new ↔ handled |
| `menu_items` | code (unique per business), name, description, price, category, tags JSON, available, position |
| `orders` | the shared columns, the paid columns, lines JSON, total (with the delivery charge), pickup_at (pickup or delivery time), note, address (empty: pickup), delivery_charge. Shown by number; unpaid → new (the delivery charge was paid, D48) → ready → collected, or cancelled |
| `doctors` | code (unique per business), name, speciality, fee, days JSON, start, end, languages JSON, description, available, position |
| `appointments` | the shared columns, the paid columns, doctor_code, doctor_name, at, deposit (the clinic's when booked). Code `A-104`; unpaid → new (the deposit was paid, D48) → confirmed → done, or cancelled |
| `products` | code (unique per business), name, description, price, was_price (before a sale; empty: not on sale, D54), category, sizes JSON, colours JSON, stock (empty: not counted), position |
| `shop_orders` | the shared columns, the paid columns, lines JSON, subtotal, delivery_charge, total, zone, address, note, tracking. Code `O-101`; unpaid → new (the delivery charge was paid; or the owner moves it) → confirmed (the shop called the buyer) → shipped → delivered or returned; new or confirmed → cancelled |

The **shared columns** (`models/taken.py`) are what the agent took for the business: conversation_id,
number (101, 102… per business and table), status, name, phone, created_at, updated_at. A status
change the table's `FLOW` doesn't allow is a 409 `invalid_transition`. The **paid columns**
(`Payable`, same file) are on what a visitor can be asked to pay for first (D47, D48): currency,
paid (minor units, online), payment_ref (the gateway's id).

Money is in minor units (paisa). Datetimes are stored in UTC; "today" is the
business's timezone. JSON columns: assign a new value to change one, because
SQLAlchemy doesn't see in-place edits.

## 9. API and streaming

| Method | Path | Does |
|---|---|---|
| GET | `/` | Dev chat page (development only) |
| GET | `/api/chat/{key}` | Agent name and greeting |
| POST | `/api/chat/{key}` | `{message, conversation_id?}` → Server-Sent Events |
| GET | `/api/chat/{key}/{conversation_id}/team` | What the team wrote from Telegram; the chat page asks every few seconds (D43) |
| POST | `/api/auth/signup`, `/api/auth/login` | A new business and its owner, or a login → `{token}` |
| GET | `/api/auth/me` | Who you are: email, role, your business |
| GET/PATCH/POST | `/api/admin/…` | Admins only (404 for owners): businesses (suspend), users (reset a password), the audit log |
| GET | `/api/desks` | The kinds of business and each one's config JSON Schema |
| GET/POST/PATCH | `/api/agent` | The business's one agent: create as a kind, read, edit; problems per field |
| POST/GET/DELETE | `/api/agent/documents` | Add pasted text (JSON) or a file (`/documents/file`, multipart), list, read one, delete (D34) |
| GET/PUT | `/api/menu` | A restaurant's menu, replaced whole |
| POST | `/api/menu/import`, `/api/doctors/import`, `/api/products/import` | The same list from a CSV file's text; problems per row, nothing saved until it's clean; `dry_run` only checks (D50); `mapping` says which of the file's columns is which of our fields (D53) |
| POST | `/api/menu/import/suggest`, `/api/doctors/import/suggest`, `/api/products/import/suggest` | The file's columns, a few sample rows, our fields, and the AI's suggested match for the owner to check (D53) |
| GET/PATCH | `/api/orders…` | A restaurant's orders, newest first (`?status=&q=`); one; move its status |
| GET/PUT | `/api/doctors` | A clinic's doctors, replaced whole |
| GET/PATCH | `/api/appointments…` | A clinic's appointment requests; one; move its status |
| GET/PUT | `/api/products` | A shop's products, with their stock, replaced whole |
| GET/PATCH | `/api/shop/orders…` | A shop's orders; one; move its status and set its `tracking` (D45) |
| GET/PATCH | `/api/messages…` | Messages for the team, from any desk; one; move its status |
| GET | `/api/conversations…` | Conversations (`?status=needs_human`), and one with what was said |
| GET/PUT/DELETE | `/api/agent/telegram` | The business's Telegram bot: its username, `t.me` link and the owner's alerts link; connect it with its token; disconnect it (D42, D43) |
| POST | `/api/telegram/{channel_id}` | Telegram's webhook: checked by its secret header, answered after the response |
| GET | `/pay/{id}` | The visitor's payment link: off to SSLCommerz to pay for an unpaid order or appointment (D47, D48) |
| POST | `/pay/{id}/back` | SSLCommerz sends the visitor back here (paid, failed or cancelled); a page says which |
| POST | `/api/payments/sslcommerz/{id}` | SSLCommerz's IPN: the same check, for when the buyer closed the tab |
| GET/PUT/DELETE | `/api/agent/sslcommerz` | The business's own SSLCommerz store: its id and test mode; connect it (checked with SSLCommerz first); disconnect it (D49). The password never comes back |
| GET | `/health` | DB reachable |

Lists that grow (orders, appointments, shop orders, messages, conversations, the
audit log) come a page at a time, newest first (D46): `?limit=` (1 to 100, default
100) and `?before=` the last row's time (`created_at`; `updated_at` for
conversations; `at` for the audit log). Fewer than `limit` rows means it was the
last page.

Everything under `/api` except chat, desks, auth and the Telegram webhook needs
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
change a table's flow doesn't allow is a 409 `invalid_transition` (§8).

SSE events: `open {conversation_id}`, `token {text}`, `status {label}`,
`handoff {message, phone}`, `done {interrupted}`, `error {message}`.

Every turn logs one JSON line, `turn finished`, with its trace: steps, timings,
tokens, cost, error (rule 7). There is no trace table (D15).

### Limits on a public chat (D51)

The chat needs no login, so `VisitorService` (`services/visitors.py`) stops timepass from
spending a business's turns. Every turn names its **visitor**: the web page keeps a random token
in the browser (`visitor_id` in the chat body, `?visitor=` on a call's offer; a 422 without it),
and Telegram's is the chat id.

```
start_turn ─► visitors.admit: blocked? the visitor's turns today ≥ VISITOR_DAILY_TURNS (30)?
                              the business's turns today ≥ WORKSPACE_DAILY_TURNS (500)?
                 yes ─► 429 visitor_limited: "…Please contact us at <hand-off phone>." No model call
                 no  ─► count the turn, run it
turn ended ─► visitors.after_turn: off_topic ran ─► a strike; OFF_TOPIC_STRIKES (5) in a row
                                                    ─► blocked for BLOCK_HOURS (24)
                                   another tool ran ─► strikes back to 0
```

A turn the team answers (D43) isn't counted. A call is checked before it connects, then each
utterance is a turn like any other; a limited one is said out loud. On Telegram the refusal is
sent as a message. The day is the business's local day. The IP address isn't used: on mobile data
in Bangladesh many people share one.

### Rate limits (D52)

The daily limits stop slow timepass; rate limits stop bursts. `RateLimiter`
(`services/rate_limits.py`, on the `limits` library) counts over a sliding window and answers a
429 `rate_limited` before anything else runs. The limits are constants at the top of the file:

| Route | Counted by | Limit |
|---|---|---|
| `POST /api/chat/{key}` | visitor_id | 6 a minute |
| `POST /api/chat/{key}` | IP (a script making a new visitor_id each time) | 60 a minute |
| `POST /api/auth/login` | IP and email | 10 in 15 minutes |

The counts are in this server's memory: a restart clears them, and a second server process would
keep its own. The IP is the real visitor's when a proxy on the same machine sends
`X-Forwarded-For` (uvicorn trusts it from `127.0.0.1` by default).

### Importing a list: their columns, our fields (D50, D53)

A business's spreadsheet has its own column names ("Item Title", "MRP", "Qty"). It doesn't have
to rename them to ours: the owner matches each of our fields to one of their columns, with the
AI's suggestion to start from. Three steps, each knowing nothing of the others:

```
their file ─► 1. read: rows as {their column: text}            csv_import.peek / parse
           ─► 2. match: {our field: their column}               the AI suggests, the owner decides
           ─► 3. a row becomes our item: Product, MenuItem…     the desk's own row(), as before
```

**A desk lists its fields once:** `PRODUCT_COLUMNS`, `MENU_COLUMNS`, `DOCTOR_COLUMNS` in its
`values.py`, each a `Column(name, about, required)`. `about` is what the field holds, in plain
words, for the owner's screen and for the model. The required ones are what `parse` needs.

**The flow:**

1. `POST /api/products/import/suggest {csv}` → the file's `headers`, its first 3 rows as
   `samples`, our `columns`, a `mapping` (`{"name": "Item Title", "price": "MRP"}`, a field with
   no column left out) and `by_ai`.
2. The owner's screen shows each of our fields with a dropdown of their columns, the AI's choice
   picked. They change what's wrong.
3. `POST /api/products/import {csv, mapping, dry_run: true}` → the first rows as they'll be
   saved, or the problems per row. Then the same without `dry_run` saves them.

Without `mapping`, the import matches columns by name as before (D50), so a file with our column
names needs no step 1.

**The AI only matches; Python checks it and does the values** (rule 1). `ImportService.suggest`
(`services/imports.py`) sends the model our fields with their `about`, the file's headers and the
3 sample rows (never the whole file), and asks for one JSON object, field → column. From the
reply, `csv_import.check_mapping` keeps only our fields, the file's columns, and each column once.
If the model fails, times out (`SUGGEST_TIMEOUT`, 20s) or answers something that isn't that
object or matches nothing, the suggestion is the columns whose name already matches ours (`by_ai: false`) and the
owner fills in the rest. An upload never fails because of the AI. Turning "৳1,850" into 185000,
"M; L" into a list or "yes" into `True` stays in the desk's `row()`.

**Later sources** (Shopify, a POS) are another step 1: something that returns rows as
`{their field: text}`. Their field names are the same for every store, so their mapping is
written once in code and the owner never sees step 2. Nothing in step 3 changes.

### Telegram (D42)

A business connects **its own bot**: it makes one with @BotFather and sends the token to
`PUT /api/agent/telegram`. `ChannelService` checks the token (`getMe`), then sets the bot's
webhook to `PUBLIC_URL/api/telegram/{channel id}` with a random secret, and saves the row.
`GET /api/agent/telegram` gives the `t.me` link the business shares with its customers.

```
Telegram ─► POST /api/telegram/{id}  (secret header checked, else 404) ─► 200 at once
         then: /start      ─► a new conversation, and the greeting is sent
               text        ─► "typing…" ─► ConversationService.reply(agent, "telegram", chat id, text)
                               ─► the whole reply as one message, then the hand-off line if any
               anything else (a photo, a voice note) ─► "I can only read text messages for now."
```

- **Which conversation:** `conversations.contact` is the chat id. A message continues the
  chat's latest conversation, unless it's full (`MAX_TURNS`) or quiet for
  `CONVERSATION_IDLE_HOURS` (12): then a new one starts, so yesterday's half-order doesn't return.
- **Another messaging app** (WhatsApp) is the same shape: its port and adapter, a webhook
  route, and `ConversationService.reply` for the turn.
- Only private chats are answered. The token is never returned by a route, and httpx's
  request log is off because Telegram's URLs carry it.

### The team on Telegram (D43)

The owner gets alerts, and can answer any web or Telegram visitor, in their own chat with the
business's bot. `services/team.py` (`TeamService`) does it.

- **Linking:** `GET /api/agent/telegram` gives `owner_link`, `t.me/<bot>?start=owner_<code>`.
  The code is made from the webhook secret, so connecting the bot again changes it. Opening
  it sends `/start owner_<code>`, and the chat id is saved as `channels.owner_chat_id`.
- **Alerts:** a tool returns `ToolResult.alert` (Python writes it: `place_order`'s order and
  read-back, and `take_message`'s) and the turn passes it on in `TurnEnded.alerts`; a hand-off (asked for, or a
  failure) is an alert with the last four lines said. After saving the turn,
  `ConversationService` hands them to the team. Every alert ends with `Ref: <conversation id>`.
  Alerts come from every channel; nothing is sent until the owner has linked their chat.

```
owner replies to an alert ─► Ref found in the quoted text ─► their words added to the
    transcript as the agent's, marked "by": "team"; the conversation becomes `human`
    ─► a Telegram visitor gets it from the bot; a web visitor's page fetches it (/team)
visitor writes while `human` ─► saved, forwarded to the owner with its Ref; no model call
owner replies /done ─► `active` again: the agent answers, and sees what the team said
```

- After a hand-off the agent keeps answering until the owner replies, so no one waits in
  silence. A phone call can't be answered this way; the owner is told to call back.
- The `by` mark is taken off before the transcript goes to the model.

### Paying first (D47–D49)

A restaurant, clinic or shop can set `pay_first`: the visitor pays online through SSLCommerz,
into the business's **own** store, before anything is placed. What's paid is the table's `Payable.due`:

| Desk | Pays first (`PAY_FOR`) | Then |
|---|---|---|
| Shop | the delivery charge | the rest in cash on delivery |
| Restaurant | the delivery charge, for a delivery (pickup pays nothing first) | the rest on delivery |
| Clinic | a deposit the owner sets, copied onto the appointment | the clinic phones to confirm |

Nothing to pay (free delivery, a pickup) saves it `new` as before. The owner turns it on and
off, sets the amounts and connects the store on the frontend's Agent → Payments section.

**Each business's own store (D49).** `payment_accounts` holds its SSLCommerz store id,
store password and test mode; `PUT /api/agent/sslcommerz` checks them with SSLCommerz first
(it opens a ৳10 session no one pays; a 400 carries SSLCommerz's reason) and no route returns
the password. The agent asks for payment only when `pay_first` is on **and** a store is
connected (`Store.pays_first`): without one, the prompt says nothing about paying, the tools
place orders as before, and `missing` says "Connect your SSLCommerz account…".
`services/payments.py` (`PaymentService`, its `PAYABLE` tables), `api/payments.py` (the
pages) and the `PaymentGateway` port (`adapters/payments/sslcommerz.py`; `PAYMENTS=fake` for
tests) do it. `desks/shared.py` has the tool's answer (`awaiting_payment`) and the prompt's
line (`pay_first_rule`).

```
yes to the read-back ─► the tool saves it `unpaid` (a shop's items held), returns
    pay_link = PUBLIC_URL/pay/<id>; no alert yet
visitor opens the link ─► a new SSLCommerz session for `due` ─► its page
SSLCommerz posts val_id back (the buyer's browser to /pay/<id>/back, and its IPN to
    /api/payments/sslcommerz/<id>)
    ─► settle(id, val_id): the business's store asks SSLCommerz's validation API: VALID,
       for this row (its tran_id), this amount
    ─► UPDATE … WHERE status = 'unpaid' → new, paid, payment_ref (only the first post wins)
    ─► "✅ Payment received…" added to the chat as the team's (the page and the model see it;
       the bot sends it on Telegram) and the owner's alert
```

- The agent can't mark anything paid: no tool moves `unpaid` (moving an unpaid appointment
  keeps it unpaid), and the prompt says nothing is placed until a message says so. What the
  browser posts is never trusted; only `val_id`, asked about with the business's own store.
- The link opens a fresh session each time, so it doesn't run out. The row's id is long and
  random: the link is its own key, like the Telegram webhook's channel id.
- The owner can still move `unpaid` → `new` (paid some other way) or cancel it.

## 10. Voice (M2)

A `voice/` package turns speech into `start_turn(channel="voice")` calls and
the turn's events back into speech, with Pipecat (D18): the browser's audio over
WebRTC (`POST /api/voice/offer`), Silero VAD, Deepgram for speech-to-text
(streamed; `STT_PROVIDER=chorus` switches to Chorus, one upload per utterance
after 0.5s of silence, D39) and Piper for text-to-speech until
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

- Unit tests for `runtime/` and `domain/` with `FakeLLM`, `FakeDecider` and the fixed clock
  (`FIXED_NOW` = Tue 29 Sep 2026 08:00 UTC = 14:00 in Dhaka).
- A test file per desk (`tests/integration/test_restaurant.py`, `test_clinic.py`,
  `test_messages.py`): its tools against its real store on in-memory SQLite, its prompt, its
  routes, and whole conversations scripted with `FakeLLM` (an order or appointment saved only
  after a yes). The `world` and `client` fixtures are in `tests/conftest.py`.
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
| Searching a long menu or doctor list | The whole list is in the prompt, every turn; fine for the few dozen a small business has (D41) |
| Several tool calls in one Gigalogy reply | The first `{"tool": …}` runs; the model sees its result and can call the next |
| Progress-line policy (thresholds, rotation) | Web shows the tool's label; voice says the tool's filler |
| What the caller *heard* after a barge-in | The turn's trace says `interrupted`; the text is what was generated |
| Usage shown to the owner, limits per plan, Cloudflare Turnstile | One `VISITOR_DAILY_TURNS` and `WORKSPACE_DAILY_TURNS` for every business (D51); cost is in each turn's log line. Clearing the browser's token makes a new web visitor, still inside the business's daily turns and the IP's rate limit (D52) |
| Real bookings (a free slot, a calendar) | The clinic takes an appointment *request*; the clinic phones to confirm |
| Teams, roles, billing, the Vue app, phone numbers, deploy | Swagger and the dev page are the UI; the demo runs locally |
| Telegram: photos, voice notes, buttons, messages over 4,096 characters | Text only, and replies are a few sentences (D42) |
| Owner takeover: a chat the owner never gives back with `/done` | It stays with the team until it's quiet for `CONVERSATION_IDLE_HOURS`; then a new conversation starts with the agent (D43) |
| Telegram: a repeated update (Telegram retries when we don't answer) | The webhook says OK before the turn runs, so a retry is rare; one would be answered twice |
| Encrypting bot tokens at rest | Stored as they are; no route returns them |
| Shop: Facebook Messenger, Instagram, WhatsApp | Web chat, voice and Telegram for now; Messenger is the next channel (D45) |
| Shop: photos of a product, stock per size and colour | The agent asks for the product's name; stock is per product |
| Import: saving a business's column match, one column split into two fields ("Size/Colour"), a limit on suggest calls, Shopify or a POS | The screen asks again on each upload (the AI's suggestion makes that quick); the owner splits the column in the sheet; suggest is for logged-in owners only, one model call a click; CSV only for now (D53) |
| Payments: refunds, a restaurant's charge per area, an unpaid order that expires, encrypting store passwords | The owner refunds in SSLCommerz's panel (a payment for a cancelled one is an alert saying so); one restaurant delivery charge to any address; the owner cancels what was never paid; passwords are stored as they are, like bot tokens, and no route returns them |
