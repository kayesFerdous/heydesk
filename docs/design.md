# HeyDesk — design

How the MVP works. One page per idea, no more. [PLAN.md](../PLAN.md) says what
to build when; this file says how. Decisions are in [decisions.md](decisions.md).

**Keep it simple.** Build the normal path well. An edge case gets code only
when it's cheap or it would break the demo; otherwise it goes in §12 *Left out*.

---

## 1. What it is

A business picks its **kind** (a restaurant, a clinic, a receptionist for an office, an online shop), fills in
its details (address, hours, its menu, doctors or products, FAQ) and adds its own notes, and it has an AI
front-desk agent. Each kind is a **desk** with its own prompt, tools and tables (D41). Visitors talk to it by **web chat** or **voice** in the
browser, or on **Telegram** with the business's own bot (D42), or on its Facebook Page's **Messenger** (D60). It answers questions, takes orders and messages, and hands over to a
person when needed. The business sees orders, appointments and messages through the API, which
the separate frontend (`../heydesk-frontend`) uses.

**The model talks; code guarantees.** The model writes the words. Python
decides prices, totals, times, what to ask next and when something may be
saved. Nothing is saved without reading it back and hearing a yes.

## 2. Words

| Word | Meaning |
|---|---|
| Desk | A kind of business (`restaurant`, `clinic`, `receptionist`, `shop`): one folder in `desks/` with its own config fields, prompt, tools, tables and owner routes (D41) |
| Agent | A business's agent: one desk's config, which the business edits. Edits are live on the next message |
| Store | A business's data as its desk sees it (`ClinicStore`: doctors, appointments, messages), always scoped to one workspace |
| Conversation | One visitor's chat or call: its `messages` are the whole transcript |
| Turn | One visitor message and everything the agent did for it |
| Channel | Where a visitor talks: `web`, `voice` or a messaging app (`telegram`, `messenger`). A messaging app's link to the business is a row in `channels` (D42, D60) |
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
├── ports/               Protocols: LanguageModel, Decider, Embedder, KnowledgeStore, PhotoIndex,
│                        MediaStore, TelegramApi, MessengerApi, PaymentGateway, the sheet reader, WooApi
├── adapters/            one per port, each with a fake: GigalogyLLM (default) and GroqLLM, JevDecider,
│                        Gemini, Qdrant (documents and photos), the Telegram Bot API, Messenger's
│                        Graph API, SSLCommerz, Google Sheets' CSV (D58), WooCommerce's REST API (D75),
│                        photo files on the disk or in Cloudflare R2 (D101)
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
├── services/            agents, conversations, users, admin, visitors (the limits, D51), rate_limits (D52),
│                        knowledge, photos (D57), imports (D53), sheets (D58), woo (D75), channels (Telegram and
│                        Messenger, D42, D60), team (the owner's alerts, D43), payments (D47)
├── api/                 routes: read the request, call one service or store, return
├── voice/               the voice channel (M2); bangla_tts.py, the Bangla voice's client (D84)
└── web/static/          the dev chat page
tts_server/              the Bangla voice: its own uv project, run beside HeyDesk (D84)
```

Imports point inwards: `api`/`voice` → `services` → `desks` → `runtime` → `ports`. A desk's
tools reach the database only through its store, and only its `api.py` knows HTTP.
`scripts/check_imports.sh` checks it.

## 5. Agents and desks

**A desk is a kind of business, written as plain Python** (D41). Each folder in `desks/` has:

| File | What's in it |
|---|---|
| `__init__.py` | The desk: its config class, its `ROLE` text, `prompt()` (its sections of the system prompt, with its facts), `note()`, `missing()` and `seed()` |
| `tools.py` | Its tools: the restaurant's `update_order`, `place_order`, `track_order`, `change_order`, `cancel_order` (D65), and `book_table` and `cancel_booking` when it takes bookings (D64); the clinic's `find_doctor`, `request_appointment`, `cancel_appointment`, `reschedule_appointment`; the shop's `find_product` (D80), `update_cart`, `place_order`, `track_order`, `change_order`, `cancel_order`, `request_exchange`, `show_photos` |
| `values.py` | Its shapes, no database: `MenuItem`, `NewOrder`, `NewBooking`, `has_room()`, `free_times()`; `Doctor` (with `works_at`), `NewAppointment`; `Product`, `Zone`, `match_zone()`, `ShopConfig`, `mobile()`; `districts.py`, Bangladesh's 64 districts and their old spellings (D98) |
| `models.py` | Its tables: `menu_items`, `orders` and `bookings`; `doctors` and `appointments`; `products` and `shop_orders` |
| `store.py` | Its data access, for its tools and its owner routes, always one workspace |
| `api.py` | Its owner routes: `/api/menu`, `/api/orders`, `/api/bookings`; `/api/doctors`, `/api/appointments`; `/api/products`, `/api/shop/orders` |

`desks/__init__.py` lists them (`DESKS`), and `tools_for(desk, config, has_documents)` is a desk's
tools for this business (`Desk.own_tools(config)`: the restaurant adds its booking tools only
when `takes_bookings` is on, D64) then the shared ones: `take_message`, `handoff_to_human` and `off_topic` (D51) always,
`search_knowledge` when the business has documents (D35). To add a kind of business: a new folder, and a line in `DESKS`.

**The config.** `runtime/config.py`'s `AgentConfig` has what every business has: name, business
name, timezone, currency, greeting, address, `hours`, the owner's **notes**, FAQ, hand-off. A desk
subclasses it: the clinic adds `emergency_phone` and `policies`, the receptionist `about` and
`services`, the shop `delivery` (its areas, each with a charge, usual days, its `districts` (of the 64, in English or Bangla, saved in English: Chittagong or চট্টগ্রাম → Chattogram), D98, and `pay_first`, D92; an area with no districts is everywhere else (D100), and there can be one, a 422 otherwise), `call_to_confirm` (default on: a cash-on-delivery order waits for the shop's call; off: it's confirmed when placed; `calls.enabled` needs it, D92), `free_delivery_over`, `policies`, `size_guide` (D54) and `exchange_days` (D55); the restaurant `delivers` and `delivery_charge` (one charge to any address), and `takes_bookings` (off by default), `seats`, `table_minutes` (default 90) and `max_party` (default 10) for table bookings, where `takes_bookings` needs `seats` (D64); the clinic `deposit`; and the restaurant and clinic each `pay_first` (D47, D48), which needs something to take (a 422 otherwise), where the shop has it per delivery area (D92); each sets its own default greeting. Every agent also has a `widget`: how the chat looks on the business's own website (D82): `colour`, `side`, `nudge` (the line that pops out of the bubble; empty: none), up to 4 `starters` (tap-to-ask buttons, each desk with its own) and `logo` (only one saved by `POST /api/agent/logo`). A field the desk doesn't have is a 422, so
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
                 ─► append the turn's messages, save and log the trace
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
| `place_order` | restaurant | **yes** | `delivery` (when the restaurant `delivers`), `time`, `address` (for a delivery), `name`, `phone`, `note`. The time must be later today and inside the hours; a delivery adds `delivery_charge` to the total. Returns the order code, `O-101` (D65), and an alert for the owner (D43). With `pay_first`, a delivery order is saved `unpaid` and it returns a `pay_link` for the delivery charge instead, and no alert; pickup pays nothing first (D48) |
| `track_order` | restaurant | no | The order placed in this chat with no code; otherwise its code and the phone it was placed with, one answer for a wrong code and a wrong phone (as the shop, D55). Its status in words, dishes, total, time; a `pay_link` while unpaid (D65) |
| `change_order` | restaurant | **yes** | Found the same way; only while `new`. A new `time` (later today, inside the hours), `address` (a delivery only) or `note`; reads back old → new. The store checks it's still `new` when saving. The dishes can't change: cancel and order again. An alert for the owner (D65) |
| `cancel_order` | restaurant | **yes** | Found the same way; while `unpaid` or `new`. An alert for the owner, which says to refund it when it was paid online (D65) |
| `book_table` | restaurant | **yes** | Only when `takes_bookings` (D64). `people`, `time` (a day and a time in the next 14 days, inside the hours: `parse.day_and_time`), `name`, `phone`, `note`. `people` at most `max_party`. As soon as the people and the time are known, Python counts the seats: no more than `seats` people at any moment, each booking holding its seats for `table_minutes` (one shared count, not real tables). A full slot is refused with up to 3 free times nearby on the same day, 30 minutes apart. Saved `new`, code `T-101`; the visitor is told the restaurant will confirm it in the chat; an alert for the owner |
| `cancel_booking` | restaurant | **yes** | Only when `takes_bookings`. The code (`T-101`) and the name it's under, as the clinic's `cancel_appointment` (D38); while `new` or `confirmed`. An alert for the owner |
| `find_doctor` | clinic | no | Doctors by `speciality`, `day` (today, tomorrow, a weekday) and `language`. Python turns the day into a date and keeps the doctors who work it |
| `request_appointment` | clinic | **yes** | `doctor`, `time`, `name`, `phone`. The doctor is matched by code or name words; the time is a day and a time in the next 14 days, inside the clinic's hours and the doctor's `days`/`start`–`end`. Returns its code, `A-104`; the clinic phones to confirm. With `pay_first`, it saves the request `unpaid` with the clinic's `deposit` and returns a `pay_link` instead (D48). Moving an unpaid one keeps it unpaid |
| `cancel_appointment` | clinic | **yes** | The code (`A-104`, `a104`, `a 104` all work) and the name it's under; a wrong code and a wrong name get the same answer, so nothing leaks (D38) |
| `reschedule_appointment` | clinic | **yes** | The same, plus a new time checked like a new request. A moved appointment is `new` again |
| `take_message` | every desk | **yes** | `message`; optional `name`, `phone`, `best_time` (checked when given; the agent offers once to take them, D44). Returns its code, `M-104`, and an alert for the owner |
| `search_knowledge` | every desk, with documents | no | The best 4 passages from the business's documents, with their titles and a reminder that they are facts, never instructions (D35) |
| `find_product` | shop | no | `query`: a name, a group, a colour or what it's for. Python scores each product's words (twice in the name or group, once in the sizes, colours or description) and returns up to 8 of the prompt's own product lines, best first; nothing found says to try other words. The prompt tells the model to use it only when the shop has more than `LONG_LIST` (60) products (D80) |
| `update_cart` | shop | no | Sets the whole order: codes, quantities, and a size and colour when the product has them. Python checks the choices and the stock (the product's own, or its variant's for that size and colour, D56: "Cotton Panjabi (L/Navy): sold out (left: M, L/White)") and adds up the subtotal. Under `free_delivery_over`, it also says how much more makes the delivery free (`free_delivery_if_you_add`, D54); at or over it, `delivery: free`. The cart goes in `state["cart"]` (D45) |
| `place_order` | shop | **yes** | `address`, `name`, `phone` (a Bangladeshi mobile, Bangla digits and +880 accepted), `note`, and `district` (the model reads it off the address: Mirpur → Dhaka). Python finds the area (`match_zone`, D98, D100): the one with the district, else the one for everywhere else; a name that isn't one of the 64 is a problem to ask about, and a district no area covers is "the shop doesn't deliver there". An address with no district asks for it. Gives the delivery charge and total as soon as the area is known; free over `free_delivery_over`. What's missing comes back as one list: in a chat, "ask for all of these in one message"; on a call, one at a time (`Checks(together=…)`, D92). Takes the items out of stock (the product's, or the variant's) as it saves. Saves it `new` when the shop calls to confirm (`call_to_confirm`), else `confirmed` (D92). Returns its code, `O-101`, what to say next (the call or that it's confirmed, the cash for the delivery man, who calls before he comes, the usual days), and an alert, which also warns when this mobile has returned or cancelled orders before and lists the order's products at `LOW_STOCK` (3) or fewer (D54). When its area has `pay_first` (and a charge, and the shop's store is connected), it saves the order `unpaid` and returns a `pay_link` instead, and no alert: the payment confirms it (D47, D92) |
| `track_order` | shop | no | No code for the one order placed in this conversation (D55); otherwise the mobile alone finds the latest order with it, or on Telegram and Messenger nothing finds this buyer's last order (D92), and the code and the mobile find any: its status in words, items, total, what was paid online and what's left for cash on delivery, how long delivery usually takes, and the `pay_link` while it's unpaid. A wrong code and a wrong number get the same answer |
| `change_order` | shop | **yes** | Found like `track_order`, except the mobile alone: a change needs the code (or the order from this chat, or this buyer's last). Then any of new `items` (the whole list, checked and priced as `update_cart`'s, with the order's own items counted as on the shelf, D92), `address` (with its `district`, or it asks for it, D98), `new_phone`, `note`, only while `new` or `confirmed` (`CHANGEABLE`). New items or a new district work out the delivery charge (free or not) and total again; not once its charge was paid online. The store swaps the stock in one transaction. Reads back only what changes; alerts the owner (D55) |
| `cancel_order` | shop | **yes** | Found like `change_order`; only while `unpaid`, `new` or `confirmed` (not once the courier has it). The items go back in stock |
| `request_exchange` | shop | **yes** | Found like `track_order`; only a `delivered` order, within `exchange_days` of being marked delivered. `product` (a code from the order) and the `size` and/or `colour` wanted, checked against the product's own (`Product.choose`, as `update_cart`) and refused when that variant is sold out, optional `reason`. Saved as a message for the team (`M-104`) with an alert: the owner arranges the swap; no stock changes (D55) |
| `show_photos` | shop | no | `products`: up to 3 codes. Sends a `photos` event with each one's name, price and first photo, and says which were shown; a product with no photo is named so the model describes it instead. Not on a call: there it says photos can't be shown here. Messenger gets a carousel (D62), Telegram a photo each (D63), each with an Order button (D99) (D57) |
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

### Photos (D57)

A shop's products have photos, a visitor can send a photo ("eta ache?"), and the agent can show
a product's photos. Web chat, Messenger (D62) and Telegram (D63); voice stays words (§12).

```
owner adds:  a file ─► Pillow: a JPEG, at most 1024 px ─► the media store: <workspace_id>/products/<id>.jpg
             ─► Gemini embeds the image (one image per request) ─► Qdrant `heydesk_photos`
             ─► {url}; the Products page puts the URL in the product's `photos` and saves
visitor sends: the page shrinks it to a JPEG ─► `photo` in the chat body (base64)
             ─► saved the same way, as <workspace_id>/chat/<id>.jpg ─► Gemini embeds it
             ─► Qdrant: this business's 10 nearest photos ─► each URL ─► its product
             ─► one line at the top of the visitor's message
agent shows:  show_photos(products) ─► a `photos` event: each product's name, price and photo
```

- **Python matches, the model only talks (rule 1).** The visitor's stored message starts with a
  line Python writes, then what they typed:
  `[Photo. Closest products in the shop: Sky Blue Casual Shirt [P12], Sky Blue Shirt [P15]]`, or
  `[Photo. Nothing in the shop looks like it.]`, or, when Gemini or Qdrant didn't answer,
  `[Photo. It couldn't be checked: ask what it is.]`. It's stored, so later turns still know what
  the photo was. A product appears once (its best photo), at most 3, best first, and only at or
  above `PHOTO_MIN_SIMILARITY` (0.75).
- **A match is never a yes.** Measured 13 Oct on 14 real products (shirts, a panjabi, phones,
  cameras): the right product came first 42 times out of 42, at 0.84–0.95; but when a product is
  missing, a look-alike still scores up to 0.86. No number tells "this one" from "one like it",
  so the prompt says: show the closest with `show_photos` and ask if it's the one; if not, offer
  the others as similar. Below 0.75 is a different kind of thing (a phone for a graphics card
  scored 0.72).
- **A message can carry photos.** A visitor's message has `photo` (its URL), and `show_photos`'s
  tool message has `photos` (its items). The chat page and the owner's conversation view show
  them. The turn leaves them out of what the model gets (`HIDDEN` in `runtime/loop.py`, with
  `by`).
- **Isolation.** Files are under the business's id; every Qdrant point has `workspace_id` and
  every search filters by it. Saving products refuses a photo URL that isn't one of this
  business's product photos (a 422). A photo found that's on none of its products (one the owner
  removed) is skipped. A CSV import keeps each product's photos by its code.
- **Only the shop takes photos** (`Desk.takes_photos`). `GET /api/chat/{key}` says `photos: true`
  for it, so the page shows the button; a photo sent to another desk is a 422.
- **Limits.** 3 photos a product. A visitor's photo is at most 2 MB after the page shrinks it,
  an owner's 10 MB, and Pillow must read it (a 422 `unreadable_photo` otherwise). The services
  are `PhotoService` (`services/photos.py`), the `PhotoIndex` port and its Qdrant adapter, which
  shares one Qdrant client with the documents (local mode lets only one open its folder). Gemini takes only PNG and JPEG, so everything is
  stored as JPEG; the owner can upload WebP too.
- **Free.** Gemini's free tier; one call per photo added and one per photo a visitor sends.
  Gemini allows 6 images per *embedding*, not per call, so a batch of one-image requests is fine.
  Free-tier inputs may be used by Google to improve its products.
- `reindex-knowledge` also re-embeds every product photo from its file.

**Where the files are (D101).** A `MediaStore` keeps them by key, `<workspace_id>/<folder>/<id>.jpg`,
and a photo's URL is the store's `url` + "/" + key:

| `R2_BUCKET` | Store | `url` | Served by |
|---|---|---|---|
| empty (dev, tests) | `DiskMedia(MEDIA_DIR)` | `/media` | this app (`main.py` mounts `/media`) |
| set | `R2Media`: Cloudflare R2, a public bucket | `R2_PUBLIC_URL` | Cloudflare |

- `R2Media` signs each S3 request itself (AWS Signature Version 4: hashlib and hmac, checked
  against Amazon's published examples), over httpx. No SDK.
- `is_products` checks the address, not the file: the business's own products folder and a
  32-hex name, so no `../` reaches another business. A network call per photo on every save
  would buy nothing more.
- Messenger fetches a card's photo from its URL: `/media/…` gets `PUBLIC_URL` in front, an R2
  URL is whole already. Telegram is uploaded the bytes (`MediaStore.get`), as before.
- Visitors' photos are in the same public bucket, under names no one can guess (128 random
  bits), as they were under `/media`.
- **Switching an existing server to R2 doesn't move its files**, and the saved URLs still say
  `/media/…`. Start R2 on a fresh database, or copy the folder up and rewrite the URLs.

## 8. Data model

Every table has `workspace_id` (except `workspaces`).

| Table | Columns |
|---|---|
| `workspaces` | id, name, status (`active`/`suspended`), suspended_reason (admins only, D70) |
| `users` | workspace_id (empty for an admin), role (`owner`/`moderator`/`admin`, D97), email, password_hash, email_verified_at (empty until the emailed code is typed, D93; for a moderator, until they join) |
| `audit_events` | actor_id, workspace_id, action, target, at: what an admin did in a business. Insert-only |
| `team_events` | workspace_id, actor_id and actor_email (no foreign key: a removed moderator's changes stay), action, target, at: each write an owner or moderator made that succeeded, for the owner (D97). Insert-only |
| `turns` | workspace_id, conversation_id, channel, llm, at, total_ms, first_output_ms, input/output tokens, cost_micros, tools, error, handed_off, interrupted: each agent turn's trace, for the admin's sums (D71). No one's words. Insert-only |
| `agents` | kind (its desk), name, public_key (unique), config JSON |
| `conversations` | agent_id, channel, contact (a messaging app's chat id, D42), status (`active`/`needs_human`/`human`: the team is answering, D43), state JSON, messages JSON |
| `visitors` | key (`web:<the page's token>`, `telegram:<chat id>`; unique per business), day (the business's local day), turns (on that day), strikes (off-topic turns in a row), blocked_until (D51) |
| `channels` | agent_id, kind (`telegram`/`messenger`), token (a bot's, or a Page access token; encrypted, D74), secret (Telegram's; empty on Messenger), address (the bot's username, or the Page's id), owner_chat_id (where alerts go, D43; Telegram only). One per agent and kind (D42, D60) |
| `sheet_links` | agent_id (unique), url, mapping JSON (our field → their column), seen JSON (each product code's stock cell as last read), checked (SHA-256 of the last text fetched), synced_at, problem (empty: fine). One per agent (D58) |
| `woo_links` | agent_id (unique), url (the store's `https://` address), key, secret (encrypted, D74), ids JSON (each product code's Woo product and variation ids), checked (SHA-256 of the last products read), synced_at, problem (empty: fine). One per agent, shops only; not with a sheet (D75) |
| `payment_accounts` | store_id, password (encrypted, D74), sandbox: the business's own SSLCommerz store, one per business (D49). No route returns the password |
| `documents` | agent_id, title, filename, kind (`text`/`markdown`/`pdf`), text, chunk_count (D34). Its chunks are in Qdrant, not here |
| `team_messages` | the shared columns, body, best_time. Code `M-104`; new ↔ handled |
| `menu_items` | code (unique per business), name, description, price, category, tags JSON, available, position |
| `orders` | the shared columns, the paid columns, lines JSON, total (with the delivery charge), pickup_at (pickup or delivery time), note, address (empty: pickup), delivery_charge. Code `O-101` (D65); unpaid → new (the delivery charge was paid, D48) → ready → collected for a pickup, or ready → out_for_delivery → delivered for a delivery (D66); unpaid, new, ready or out_for_delivery → cancelled |
| `bookings` | the shared columns, at (UTC), people, note. Code `T-101`; new → confirmed → seated → done; new or confirmed → cancelled; confirmed → no_show. New, confirmed and seated hold their seats (D64) |
| `doctors` | code (unique per business), name, speciality, fee, days JSON, start, end, languages JSON, description, available, position |
| `appointments` | the shared columns, the paid columns, doctor_code, doctor_name, at, deposit (the clinic's when booked). Code `A-104`; unpaid → new (the deposit was paid, D48) → confirmed → done, or cancelled |
| `products` | code (unique per business), name, description, price, was_price (before a sale; empty: not on sale, D54), category, sizes JSON, colours JSON, stock (empty: not counted), variants JSON (`{size, colour, stock}` per size and colour; an empty size or colour means any; when there are any they hold the stock and `stock` is empty; a size and colour none matches isn't counted, D56), photos JSON (up to 3 photo URLs, the media store's url + `/<workspace_id>/products/<id>.jpg`; their vectors are in Qdrant, D57, D101), position |
| `shop_orders` | the shared columns, the paid columns, lines JSON, subtotal, delivery_charge, total, zone, address, note, woo_id and woo_sent (the order in a linked WooCommerce store, and our status as last written there; "" never, D75). Code `O-101`; unpaid → confirmed (the delivery charge was paid, which confirms it; or the owner moves it, D92); new → confirmed (the shop called the buyer); placed `confirmed` when the shop doesn't call (D92); confirmed → shipped → delivered or returned; unpaid, new or confirmed → cancelled |

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
| GET | `/api/chat/{key}` | Agent name, greeting, `photos` (true when it takes photos, D57), and `widget` (the website chat's look, its nudge and starters filled in like the greeting, D82) |
| POST | `/api/chat/{key}` | `{message, conversation_id?, visitor_id, photo?}` → Server-Sent Events; `photo` is a JPEG or PNG in base64 (D57) |
| GET | `/api/chat/{key}/{conversation_id}/team` | What the team wrote from Telegram; the chat page asks every few seconds (D43) |
| POST | `/api/auth/signup` | A new business and its owner → 204, and an email with a 6-digit code; no token until it's typed (D93, D94) |
| POST | `/api/auth/login` | → `{token}`; a 401 for a wrong email or password; a 403 `email_not_verified` for the right one before the code is typed (D94) |
| POST | `/api/auth/verify` | `{email, code}`: the code from the signup email → `{token}`, the first login; a 403 if it's wrong or expired |
| POST | `/api/auth/verify/resend` | `{email}` → 204, always: another email with the code if the account exists and isn't verified |
| POST | `/api/auth/forgot` | `{email}` → 204, always: an email with a reset code if the account exists (D93) |
| POST | `/api/auth/reset` | `{email, code, new_password}` → `{token}`; a 403 if the code is wrong, expired or used |
| GET | `/api/auth/me` | Who you are: email, role (`owner`, `moderator` or `admin`), your business |
| PUT | `/api/auth/password` | Your own password: `{current_password, new_password}` → 204; a 403 if the current one is wrong (D68) |
| GET/POST/DELETE | `/api/team/members` | The owner's team (D97): everyone in the business (`active`, or `invited` until they join); add a moderator by `{email}` → 201, and an email with a code; `POST …/{id}/invite` sends it again; `DELETE …/{id}` removes one, out at once. A 409 for an email with an account, or past 10 moderators |
| GET | `/api/team/activity` | What the owners and moderators changed, newest first, paged (D97) |
| GET/PATCH/POST | `/api/admin/…` | Admins only (404 for owners): businesses (suspend), users (reset an owner's password, never an admin's: D68), the audit log. `GET /api/admin/businesses/{id}`: one business at a glance (D69). `GET /api/admin/overview?days=7`: what every agent did (D72). `GET /api/admin/failed-turns`: turns that ended with the polite line (D73) |
| GET | `/api/desks` | The kinds of business and each one's config JSON Schema |
| GET/POST/PATCH | `/api/agent` | The business's one agent: create as a kind, read, edit; problems per field |
| POST | `/api/agent/logo` | The business's logo (multipart): saved as a 256 px JPEG (transparent is white) and set as `widget.logo` → `{url}` (D82) |
| POST/GET/DELETE | `/api/agent/documents` | Add pasted text (JSON) or a file (`/documents/file`, multipart), list, read one, delete (D34) |
| GET/PUT | `/api/menu` | A restaurant's menu, replaced whole |
| POST | `/api/menu/import`, `/api/doctors/import`, `/api/products/import` | The same list from a CSV file's text; problems per row, nothing saved until it's clean; `dry_run` only checks (D50); `mapping` says which of the file's columns is which of our fields (D53) |
| POST | `/api/menu/import/suggest`, `/api/doctors/import/suggest`, `/api/products/import/suggest` | The file's columns, a few sample rows, our fields, and the AI's suggested match for the owner to check (D53) |
| GET/PATCH | `/api/orders…` | A restaurant's orders, newest first (`?status=&q=`); one; move its status (the visitor hears ready, on its way or cancelled in their chat, D66) |
| GET/PATCH | `/api/bookings…` | A restaurant's table bookings, newest first (`?status=&q=`); one; move its status (D64; the visitor hears confirmed or cancelled in their chat, D66) |
| GET/PUT | `/api/doctors` | A clinic's doctors, replaced whole |
| GET/PATCH | `/api/appointments…` | A clinic's appointment requests; one; move its status |
| GET/PUT | `/api/products` | A shop's products, with their stock and `variants`, replaced whole. A variant's size or colour that isn't the product's is a 422 |
| POST | `/api/products/photos` | A product photo (multipart): saved as a JPEG and embedded → `{url}`; the product keeps it once its `photos` are saved with that URL (D57) |
| GET | `/media/…` | When the photos are on this server's disk, not in R2 (D101). A photo: a product's (`<workspace_id>/products/<id>.jpg`) one a visitor sent (`<workspace_id>/chat/<id>.jpg`) (D57), or the business's logo (`<workspace_id>/logo/<id>.jpg`, D82) |
| GET/PATCH | `/api/shop/orders…` | A shop's orders; one; move its status (D45, D96) |
| GET/PATCH | `/api/messages…` | Messages for the team, from any desk; one; move its status |
| GET/PATCH | `/api/conversations…` | Conversations (`?status=needs_human`), and one with what was said: each line's `photo` (the visitor's) and `photos` (shown by the agent), D57; `PATCH {status: "active"}` marks a hand-off handled (D83) |
| GET/PUT/DELETE | `/api/agent/telegram` | The business's Telegram bot: its username, `t.me` link and the owner's alerts link; connect it with its token; disconnect it (D42, D43) |
| GET/PUT/DELETE, POST | `/api/agent/sheet`, `/api/agent/sheet/suggest`, `/api/agent/sheet/sync` | The list's linked Google Sheet: its state; link it with a checked mapping; unlink it; suggest a mapping; sync now (D58) |
| GET/PUT/DELETE, POST | `/api/agent/woo`, `/api/agent/woo/sync` | The shop's linked WooCommerce store: its state; link it with a Read/Write key; unlink it; sync now (D75) |
| POST | `/api/woo/hook/{workspace_id}` | A linked store's webhook: checked by `X-WC-Webhook-Signature` with the link's secret, a sync started 2 seconds later (D78) |
| POST | `/api/telegram/{channel_id}` | Telegram's webhook: checked by its secret header, answered after the response |
| GET/PUT/DELETE | `/api/agent/messenger` | The business's Facebook Page: its id and `m.me` link; connect it with its id and a Page access token; disconnect it (D60, D61) |
| GET | `/api/messenger` | Facebook's webhook check: echoes `hub.challenge` when `hub.verify_token` is `MESSENGER_VERIFY_TOKEN` |
| POST | `/api/messenger` | Messenger's webhook, one for every Page: checked by `X-Hub-Signature-256` with `META_APP_SECRET`, each message routed by its Page id, answered after the response |
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

Everything under `/api` except chat, desks, auth and the Telegram, Messenger and Woo webhooks needs
`Authorization: Bearer <token>`.

**Three roles** (D23, D97). An **owner** belongs to one business: the token says which
(rule 6), and they only ever see it. A **moderator** is someone the owner added to it:
they run the inbox (conversations, messages, orders, bookings, appointments, calls, the
lists) and can read the settings, but a change under `/api/agent`, the Telegram bot (its
link makes you the one who gets the alerts) and `/api/team` are a 403 for them
(`deps.owners_only`). The owner adds one by
email; the user is made with a password no one knows and the email has a reset code, so
they join with `POST /api/auth/reset` (or, once it's expired, Forgot password), which
verifies them. One account per email, so an email that has one can't be added. Removing a
moderator deletes the user, and the database is asked on every one of their requests, so
they're out at once. Every write an owner or moderator makes, once the route succeeds, is a
`team_events` row (the route and the ids in its path; the dependency writes it after the
route returns), which the owner reads at `/api/team/activity`. An **admin** runs the platform and belongs to
none: made only by `uv run heydesk create-admin EMAIL [PASSWORD]`, never by signup.
The command line also lists admins (`admins`), deletes one with no audit entries
(`delete-admin EMAIL`), and replaces a forgotten password, an admin's or an owner's
(`reset-password EMAIL [PASSWORD]`). Without a password one is made up; it is
printed, as it can't be read back from its hash (D67).
An admin works inside a business by sending its id in an `X-Business` header on
the ordinary routes (an owner's header is ignored). The database, not the token,
says someone is still an admin. Every admin write, and every admin read of one
thing (one conversation), is saved to `audit_events`. A suspended business's chat
answers 404 `unavailable`; its owner can still log in.
**Email codes** (D93, D94). Signup emails a 6-digit code and gives no token: the owner can't
log in until they type it (`POST /api/auth/verify`, which logs them in). Login with the right
password before that is a 403 `email_not_verified`, so the client shows the code screen, which
can ask for another email (`/verify/resend`). A forgotten password is
`POST /api/auth/forgot` then `POST /api/auth/reset` with the emailed code, which also verifies
the email. No code is stored: it's an HMAC (with `JWT_SECRET`) of the user, the purpose
(`verify` or `reset`), their password hash and a 15-minute window, made again to check it, so
it works for 15 to 30 minutes and a reset code is spent when the password changes. Email goes
through the `Mailer` port (an HTML email, `services/email_code.html`, with the same words as plain text): Brevo's API (`BREVO_API_KEY`, from `MAIL_FROM`, both required in
production), or, without a key, printed in the terminal. A user made on the command line is
verified already, and so is everyone from before D93 (the migration).
An admin's page for one business (`GET /api/admin/businesses/{id}`, D69) has its
desk, agent and chat key, owners, connected channels, payments (sandbox or live),
documents, a linked sheet's last sync and problem, and counts of conversations (all,
the last 7 days, waiting for a person): counts and connections, never a customer's words,
so opening it isn't audited.
Suspending takes a reason (`PATCH /api/admin/businesses/{id}` `{status, reason}`; none is a
422). It's kept on the business until it's reactivated, and is in the audit entry, for
admins only: the owner's console says it's suspended, not why (D70).
The admin's **Overview** (`GET /api/admin/overview?days=7`, 1 to 30, D72) adds up the saved
turns (D71) for today and the last N days: turns, conversations started, tokens, cost (priced
providers only), failed turns, hand-offs, and the median and 95th-percentile first reply; one
row a day, oldest first, empty days included; the 5 businesses with the most turns. A day is
Dhaka's, as every business is there today. The rows are summed in Python, not SQL.
A business's page also has its own last 7 days, the same sums (`usage`). **Failed turns**
(`GET /api/admin/failed-turns?workspace_id=`, newest first, `limit` and `before` as the audit
log, D73) are the turns with an error: when, the business and its status, the conversation, the
channel and provider, the error. The admin opens one as the business (A1) at
`/conversations?open=<id>`, which the audit log records like any opened conversation.
`uv run heydesk create-user EMAIL PASSWORD` adds an owner to the dev workspace.

Every error is `{"error": {"code", "message"}}`, with `fields` (path → problem)
for a 422. A conversation takes at most `MAX_TURNS` visitor messages (D21). A status
change a table's flow doesn't allow is a 409 `invalid_transition` (§8).

SSE events: `open {conversation_id}`, `token {text}`, `status {label}`,
`handoff {message, phone}`, `photos {items: [{name, price, url}]}` (D57), `done {interrupted}`, `error {message}`.

Every turn logs one JSON line, `turn finished`, with its trace: steps, timings,
tokens, cost, error (rule 7). The same save adds a row to `turns` (D71, changing D15's
"no trace table"): the sums without the steps or anyone's words, for the admin.

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
| `POST /api/auth/signup` | IP (a script making businesses) | 5 an hour |
| `POST /api/auth/forgot`, `/verify/resend` | email, and IP: a full inbox, our 300 free emails a day | 3 an hour, 10 an hour |
| `POST /api/auth/verify`, `/reset` | email: guessing the 6 digits | 5 in 15 minutes |

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

**One row per size and colour (D59).** Many shops keep a row for each ("T-Shirt, M, Black, 100"
and "T-Shirt, L, White, 80"). The shop's `products_from_csv` makes rows with the same name
(any case) and price one product (`grouped`): its sizes and colours are all the rows', each
row's count is a variant (D56), and the first row gives the code, description and category.
When every row has a count, a size and colour no row has gets a variant at 0: a T-shirt in
M/Black and L/White lists M and L, Black and White, but L/Black was never made, and unlisted
would mean "not counted" (D56), which the agent offered as available. A row with no count adds
no variant. A different price
stays its own product: a product has one price. A sheet gets the same, since it's read by the
same function.

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
written once in code and the owner never sees step 2. Nothing in step 3 changes. The first
one is a Google Sheet that stays linked (D58, below); the second, a WooCommerce store (D75).

### A linked Google Sheet (D58)

Most small shops and chambers keep their list on paper or in Excel, not in software with an
API. A Google Sheet is the next step up for them, and it's an import that repeats: the same
three steps as above, every few minutes, with the owner's match saved. The list it fills is
the desk's own (`products`, `menu_items`, `doctors`), so the agent, its tools and the prompt
don't change: they read our table as before.

```
owner: pastes the sheet's link ─► suggest (as D53) ─► checks the match ─► PUT /api/agent/sheet
every SHEET_SYNC_MINUTES (5):
  fetch the sheet as CSV ─► same text as last time? stop
                         ─► parse with the saved mapping (the desk's row(), as an import)
                         ─► a row has a problem? keep the old list, note it, alert the owner once
                         ─► merge (the shop's stock, below) ─► replace the list
```

- **The link.** The owner shares the sheet as *Anyone with the link: Viewer* and pastes its
  normal URL. `sheet_csv_url` turns `docs.google.com/spreadsheets/d/<id>/edit#gid=<gid>` into
  `…/d/<id>/export?format=csv&gid=<gid>`: one tab of the sheet, as CSV, with no Google keys or
  login. Only `docs.google.com` links are taken. A private sheet answers with Google's login
  page, not CSV, and connecting says to share it. Anyone with the link can read the sheet, so
  the owner's screen says to keep cost prices and buyers out of that tab.
- **Fetching** is `fetch_csv(url) -> str` (`adapters/sheets.py`, httpx, 10s; the `FetchCsv`
  port raises `SheetUnreadable` with a line for the owner), passed in by `wiring.py`; tests
  set `SheetService.fetch` to a fake that returns a string. A loop started in the lifespan
  (`SheetService.run`) syncs every linked sheet each `SHEET_SYNC_MINUTES`, one after another,
  and stops with the app. `POST /api/agent/sheet/sync` syncs one now.
- **Same text, no work.** `checked` is the SHA-256 of the last text fetched. Most syncs stop
  there: no parse, no write.
- **Everything comes from the sheet, except the shop's stock.** Names, prices, sizes, a
  doctor's days, `available`: the sheet's, every time, since nothing else changes them. Stock
  is changed by both: the owner in the sheet, and our orders (`place_order` takes it off,
  cancelling puts it back). So a product's stock comes from the sheet **only when its stock
  cell changed** since the last sync: the owner counted or restocked it. Otherwise ours stays,
  with our orders taken off since. `seen` keeps each code's stock cell as last read.
- **The owner writes what's on the shelf; Python takes off what's sold but still there.** An
  order that's `unpaid`, `new` or `confirmed` holds its items but hasn't left the shop yet, so
  a count of the shelf still includes them. When a count comes from the sheet, those orders'
  lines are taken off it (with `Product.counter`, as `place_order` takes stock, D56); never
  below 0. The owner just counts what they see, with no sum to do; a shipped order has left
  the shelf, so it isn't taken off again.

  ```python
  HOLDS = {"unpaid", "new", "confirmed"}  # sold, still on the shelf

  def merge(found: list[Product], ours: dict[str, Product], seen: dict[str, str],
            cells: dict[str, str], held: list[dict]) -> list[Product]:
      """`cells`: each code's stock cell in the sheet now. `held`: the lines of the
      orders in HOLDS."""
      keep = []
      for p in found:
          old = ours.get(p.code)
          if old and seen.get(p.code) == cells[p.code]:      # the owner didn't touch the count
              p = p.model_copy(update={"stock": old.stock, "variants": old.variants})
          else:                                              # a new count of the shelf
              p = take_off(p, [l for l in held if l["code"] == p.code])
          keep.append(p.model_copy(update={"photos": old.photos if old else []}))
      return keep
  ```

  A new product, or one whose cell changed, takes the sheet's count less what's held. A
  changed price alone doesn't reset the count. A product gone from the sheet is gone from the
  list; past orders keep their own lines. The first sync takes every count from the sheet,
  less what's held. The menu and doctors have no stock, so they are only replaced. The owner's
  screen says it in one line: *"Write what's on your shelf. We take off the orders you haven't
  shipped yet."*
- **A problem keeps the old list.** A row the desk's `row()` can't read (as an import: nothing
  saved until the whole sheet is clean), or a sheet that can't be fetched: the list stays as
  it was, `problem` says why (the first 3 rows' problems, or "couldn't open the sheet"), and the
  owner gets one Telegram alert when the problem first appears, not every 5 minutes (a new
  `TeamService.notify(ws, text)`: an alert with no conversation, so no `Ref:` line).
  A sheet that can't be fetched is read whole again once it's back.
  The next clean sync clears it.
- **While a sheet is linked, the sheet is the list.** `PUT /api/menu`, `PUT /api/doctors` and
  every CSV import are a 409 `sheet_linked` (`Store.not_linked`), so an edit in HeyDesk isn't
  silently undone 5 minutes later. Photos are still added in HeyDesk, so `PUT /api/products`
  takes only each product's `photos` then; they're kept by code.
- **One sheet per agent**, because an agent is one desk with one list. The desk names its
  fields (`Desk.columns`, the same as the import's) and its store takes the sheet's text
  (`Store.take_sheet(text, mapping, seen) -> seen`): the menu and doctors are replaced; the
  shop merges, in one transaction with reading the orders that hold stock (`merge`,
  `take_off`, `HOLDS` in `values.py`). The receptionist has no list, so no sheet.

**Routes:**

| Method | Path | What |
|---|---|---|
| POST | `/api/agent/sheet/suggest` | `{url}` → fetches it, then the same as D53's suggest: its columns, 3 rows, our fields, the AI's `mapping` |
| PUT | `/api/agent/sheet` | `{url, mapping, dry_run}` → reads the whole sheet like an import and answers like one (`saved`, `count`, `preview`): problems per row and nothing saved; `dry_run` only checks; otherwise the link is saved and the list taken from it |
| GET | `/api/agent/sheet` | The link, `synced_at`, `problem` |
| DELETE | `/api/agent/sheet` | Stops syncing. The list stays as it is and can be edited again |
| POST | `/api/agent/sheet/sync` | Syncs now; returns what GET does |

### A linked WooCommerce store (D75)

A shop with a website usually runs it on WooCommerce. Linked, its products are the shop's list,
as with a sheet, and **our orders are written into WooCommerce**, so its stock takes them off
and the owner sees every order in one place, the website's and the agent's. Shop desk only.

```
owner: pastes the store's address, a Read/Write key and its secret ─► PUT /api/agent/woo
  ─► we make 5 webhooks in the store with that key (D78)
a chat turn saves or changes an order ─► push now, in the background (D77)
a webhook post (a product or an order changed in Woo) ─► sync 2 seconds later
every 30 minutes (every WOO_SYNC_MINUTES when there are no webhooks): sync, and put back the webhooks
sync:
  push: each order whose status changed since it was last written ─► create or update it in Woo
  pull: Woo's published products ─► same as last time? stop
                                  ─► our list, less what's held by orders not in Woo yet
```

- **The link.** The owner makes a key in WooCommerce (*Settings → Advanced → REST API → Add
  key*, permissions **Read/Write**) and pastes the store's address (`https://…`, nothing else
  is taken: the key goes in a header) with the key and secret. Linking checks both: listing
  products (a 401: "check the key and secret"), and `POST /orders` with a status Woo doesn't
  know, which a Read/Write key gets a 400 for and a Read key a 401 ("make the key Read/Write"),
  so nothing is written. The secret is encrypted like the other secrets (D74). A shop has a
  sheet or a store, not both: linking one while the other is linked is a 409.
- **The API** is `/wp-json/wc/v3/` with the key as Basic auth, over HTTPS (Woo takes it on no
  other address: not `?rest_route=`, not plain HTTP). In development only, `http://localhost`
  and `http://127.0.0.1` are taken too: the test shop in `dev/woo-shop` counts its API calls as
  HTTPS, so no proxy or certificate is needed (D76). `adapters/woo.py` (httpx, 10s) behind the
  `WooApi` port: `products(store)` (published only, 100 a page, each variable product's
  variations fetched with it), `can_write(store)` (the check above), `create_order(store, body)
  -> id`, `set_status(store, id, status)`. It raises `WooUnreachable` with a line for the owner. Tests use a fake.
- **Their fields are the same in every store, so the match is in code** (`products_from_woo`,
  `shop/values.py`), and the owner never sees a mapping:

  | Ours | Woo's |
  |---|---|
  | code | `sku`, else `woo-<id>` |
  | name, category | `name`; the first of `categories` |
  | description | `short_description`, else `description`, without its HTML; other attributes ("Fabric: Linen") added as a line |
  | price, was_price | `price`; `regular_price` when it's more than `price` (on sale, D54) |
  | sizes, colours | the attribute named Size, and the one named Colour or Color (any case) |
  | stock | `stock_quantity` when `manage_stock`, never below 0; else 0 when `outofstock`, else not counted |
  | variants | a variable product's variations: `{size, colour, stock}` each, the same way (D56) |

  A variable product whose variations cost differently is one product per price, as rows are
  grouped (D59): the cheapest keeps the name, the others are named with their sizes or colours
  ("Linen Panjabi (XXL)") and coded `<code>-<price>`. Photos stay ours, kept by code, as with a
  sheet: the owner adds them in HeyDesk (D57).
  `woo_links.ids` keeps each code's Woo ids (`{code: {"id": 11, "variations": {"M|White": 15}}}`),
  for writing its order lines.
- **Woo's stock is the count.** Woo takes off its website's orders, and ours once they're
  written, and puts back what's cancelled (seen on WooCommerce 9.4: an order created `on-hold`
  or `processing` takes its stock once; `cancelled` puts it back). So the pull takes Woo's
  count as it is, with no `seen` and no shelf sum (D58's `merge` isn't used), **less the orders
  that hold stock here but aren't in Woo yet** (`HOLDS` with no `woo_id`, `take_off`). That's
  what keeps a pull from undoing an order placed a minute ago. `place_order` takes stock here
  as always, so two buyers in the same minute still can't get the last one.
- **Push, then pull.** Each sync first writes the orders that changed: `shop_orders.woo_sent`
  is our status as last written ("" never). An order placed since the link was made whose
  `status != woo_sent` is created (no `woo_id` yet) or moved:

  ```python
  WOO_STATUS = {  # ours → Woo's. Every one but returned holds or settles Woo's stock
      "unpaid": "on-hold", "new": "processing", "confirmed": "processing",
      "shipped": "processing", "delivered": "completed", "cancelled": "cancelled",
  }  # returned: left as it is in Woo; the owner restocks there, as here
  ```

  An order cancelled before it was ever written is just marked sent. The body is the buyer as
  billing and shipping (`first_name` the whole name, `phone`, `address_1` the address, `city`
  the zone), `payment_method: cod`, `customer_note` the note, each line with its product and
  variation id and **our** `subtotal`/`total` (the price the buyer said yes to, rule 1; Woo
  keeps it), the delivery charge as a shipping line, and `meta_data` with the order's code
  (`O-106`) and, when it was paid online, the amount and reference. **A chat turn that saved or
  changed an order pushes at once** (`WooService.push_soon`, started after the turn's alerts),
  in the background, so Woo's stock drops within a second or two and its website can't sell
  the same last piece; the chat never waits on the store (D77). A lock keeps one push at a
  time, so the sync and a turn never write the same order twice. A push that fails says
  nothing: the sync tries again and tells the owner. A move the owner makes in HeyDesk is
  written by the sync, at most `WOO_SYNC_MINUTES` later.
- **Woo's webhooks say when to sync** (D78). Linking makes five in the store with the owner's
  key, so the owner does nothing more: `product.created`, `product.updated`, `product.deleted`,
  `order.created`, `order.updated`, each posting to `PUBLIC_URL/api/woo/hook/{workspace id}`
  and signed with a secret made for the link (`hook_secret`, encrypted; the ids in `hooks`).
  A post is checked (`X-WC-Webhook-Signature`: base64 of its HMAC-SHA256) and answered at
  once; the sync starts 2 seconds later, and the posts that come meanwhile (an order and each
  product it moved) join it. The body isn't read: a post only means "something changed".
  Woo's ping when a webhook is made has no signature and gets a 200. Unlinking deletes them.
  Woo sends them from its own queue, so one can be late, and turns one off after 5 failed
  posts; so every 30 minutes a **safety sync** runs anyway, and `keep_hooks` turns a webhook
  back on, points one at a new address (a new tunnel) and makes again one deleted in Woo's
  admin. A store linked without webhooks (before D78, or before there was a public address)
  gets them all at its next sync (D79). Without an `https://` `PUBLIC_URL` (Woo won't post to localhost) no webhooks are
  made and the store is synced every `WOO_SYNC_MINUTES`, as before.
- **A problem is noted and the owner hears once**, as with a sheet (`problem`,
  `TeamService.notify`): the store can't be reached, the key stopped working, or an order
  couldn't be written ("O-106 wasn't added to WooCommerce: …"). The orders not written are tried
  again next sync, and the list stays as it was until a pull works. The next clean sync clears it.
- **While linked, the store is the list:** the same 409 `sheet_linked` (`Store.not_linked`), and
  `PUT /api/products` takes only photos. The owner moves orders in HeyDesk as before; each move
  reaches Woo with the next push. A change made to our order in Woo's admin isn't read back.

**Routes:**

| Method | Path | What |
|---|---|---|
| PUT | `/api/agent/woo` | `{url, key, secret}` → checks the key (above), saves the link and takes the list; answers like GET |
| GET | `/api/agent/woo` | The store's address, `synced_at`, `problem`, how many products. Never the key or secret |
| DELETE | `/api/agent/woo` | Stops syncing. The list stays and can be edited again; orders already in Woo stay there |
| POST | `/api/agent/woo/sync` | Syncs now (push, then pull); returns what GET does |
| POST | `/api/woo/hook/{workspace_id}` | Woo's webhooks: a signed post starts a sync 2 seconds later; no login (D78) |

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
               a photo (the shop) ─► the same, with the photo and its caption (D63)
               anything else (a voice note, a sticker) ─► "I can only read text messages for now."
```

- **Which conversation:** `conversations.contact` is the chat id. A message continues the
  chat's latest conversation, unless it's full (`MAX_TURNS`) or quiet for
  `CONVERSATION_IDLE_HOURS` (12): then a new one starts, so yesterday's half-order doesn't return.
- **Another messaging app** (WhatsApp) is the same shape: its port and adapter, a webhook
  route, and `ConversationService.reply` for the turn.
- Only private chats are answered. The token is never returned by a route, and httpx's
  request log is off because Telegram's URLs carry it.

### Messenger (D60)

Messenger is the same shape as Telegram, with one difference: there is one Meta app (ours), so
there is **one webhook for every Page**, set once in the app's dashboard to
`PUBLIC_URL/api/messenger` with `MESSENGER_VERIFY_TOKEN`. Facebook signs each post with
`META_APP_SECRET`; a post without a good signature is a 404.

A business connects **its Page** with the Page's id and a Page access token
(`PUT /api/agent/messenger {token, page_id}`, D61). `ChannelService` refuses a Page another
business has, subscribes our app to its `messages` (Facebook only accepts that for the right
token and Page, so it is the check), and saves the row with the Page id as `address`. There is
no `/me` call: Meta wants App Review before a dashboard token may read the Page itself.

```
Facebook ─► POST /api/messenger (signature checked, else 404) ─► 200 at once
          then, for each message: its Page id ─► the channel ─► the same turn as Telegram's
          (the sender's PSID is the contact; the Page's own echoes are skipped)
```

- **Alerts stay on Telegram** (D43): the owner answers a Messenger visitor by replying to its
  alert there, and the Page sends their words. Facebook only lets a Page answer within 24 hours
  of the visitor's last message; a later reply fails and is logged.
- The token goes in the Graph API's query string, so it's never logged (httpx's log is off).

### Photos on Messenger (D62)

The shop's photos (D57) work on Messenger as they do in the web chat: a buyer sends a
screenshot ("eta ache?"), and the agent shows products with their photo, name and price.

```
buyer sends: a message with an `image` attachment ─► its first image's URL (Facebook's CDN)
             ─► MessengerApi.download(url) ─► bytes ─► ConversationService.reply(…, photo=bytes)
             ─► the same turn as the web chat's photo (saved, embedded, the [Photo. …] line)
agent shows: show_photos(products) ─► a Photos event ─► Reply.photos
             ─► MessengerApi.send_cards: one carousel of up to 3 cards, before the reply's text
                (each card: the photo's URL, made whole with PUBLIC_URL when it's /media/…, D101;
                the name, the price)
```

- **Only the shop, only images.** A sticker arrives as an `image` with a `sticker_id`, so it's
  skipped; audio, video, files and the other desks still get "I can only read text".
  Several images in one message: the first is used (one photo a message, as in D57). Facebook
  sends a photo and its words as two messages, so the photo's turn has no text, which the turn
  allows.
- **Downloaded at once.** Facebook's attachment URLs expire, so the photo is fetched in the
  webhook's background task and stored under the business's folder like a web photo
  (`PhotoService` refuses one over 10 MB, and the visitor is told). The URL is never logged.
- **A carousel, not loose images.** Messenger's generic template shows each photo with a title
  (the name) and a subtitle (the price) in one message, like the web chat's cards. The image is
  square (`image_aspect_ratio: square`). Facebook fetches the image itself, so it needs a public
  `PUBLIC_URL`, as the webhook does.
- **`show_photos` works everywhere but a call.** If the carousel fails to send, it's logged and
  the text still goes.
- The port gains `download(url) -> bytes` and `send_cards(token, psid, cards)`; `FakeMessenger`
  records both.

### Photos on Telegram (D63)

The same as Messenger's, in Telegram's way:

```
buyer sends: a message with `photo` (the same picture in a few sizes, largest last)
             ─► TelegramApi.download(token, the largest's file_id): getFile, then the file
             ─► ConversationService.reply(…, the caption as its words, photo=bytes)
agent shows: Reply.photos ─► for each: PhotoService.read(url) ─► TelegramApi.send_photo,
             captioned "Cotton Kurti · ৳950", before the reply's text
```

- **Uploaded, not linked.** The bot uploads each photo from our media store, so Telegram needs
  no public URL for them (Messenger does). Up to 3, so one `sendPhoto` each, not an album.
- **The caption is the words.** Telegram sends a photo and its caption in one message, so
  "eta ache?" comes with the photo, unlike Messenger.
- A photo sent as a file (a document), a sticker or a voice note still gets "I can only read
  text"; so does a photo to a desk that takes none. Bots can download up to 20 MB; ours stops
  at 10 MB. The port gains `download(token, file_id)` and `send_photo(token, chat_id, jpeg,
  caption)`; `ChannelService` takes `PhotoService` to read the files.

### Buttons on Messenger and Telegram (D99)

Buyers on a phone don't like typing, so the reply can carry buttons. **A tap is only words**: it
comes back as the visitor's message, and the turn runs as if they had typed it.

```
a tool's result ─► ToolResult.choices (Python picks them) ─► the last tool's, on TurnEnded
                ─► Reply.choices ─► buttons under the reply's text
   Messenger: quick replies; a tap is a message whose text is the choice
   Telegram: an inline keyboard, three to a row; a tap is a callback_query whose data is the
             choice ─► answerCallbackQuery, then its data is the message's text

show_photos ─► each card gets an Order button whose tap says "Order [kurti]"
   Messenger: a postback button on the carousel's card ─► the postback's payload is the text
   Telegram: an inline button under each photo ─► a callback_query, as above
```

- **Which buttons:** `update_cart` missing a size or colour offers the ones not sold out, with
  the colour or size already chosen (`Product.in_stock`); `ask_for_yes` offers Yes and Change,
  for every desk. The last tool that ran in the turn decides; a hand-off or a stop sends none.
- **A Yes button is a typed yes** (rule 2): the next `place_order` still asks Jev whether the
  agent's last message was the read-back, so a Yes tapped from an old message saves nothing.
- **The limits:** a choice is at most 20 characters (Messenger's quick reply) and 64 bytes
  (Telegram's button data), at most 13 of them; a longer one gets no button and is typed. An
  Order button needs "Order [code]" within 64 bytes.
- **Web chat and voice** ignore the choices: the web chat's cards are unchanged (its photos
  now carry the `code`), and a call stays words.
- **Postbacks are signed** like messages: they come to the same `/api/messenger`. Our app
  subscribes a Page to `messages,messaging_postbacks`, and the Meta app's webhook needs the
  `messaging_postbacks` field. Telegram's webhook allows `message` and `callback_query`. A Page
  or bot connected before D99 is connected again once to get them.
- The model is told, in the Telegram and Messenger channel lines, that a tap arrives as its
  words, and to reply in the language they've been writing in (the buttons are English).

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

- **Telling a restaurant's visitor (D66):** when the owner moves an order or a booking with
  `PATCH /api/orders/{id}` or `/api/bookings/{id}`, the row's `news()` gives a line, and
  `TeamService.tell_visitor` adds it to that conversation as the team's (the web page fetches
  it, the bot or the Page sends it, the model sees it next turn; the conversation stays with
  the agent). Lines: a pickup `ready` ("Your order O-101 is ready to pick up."), a delivery
  `out_for_delivery` ("…is on its way."), a booking `confirmed` ("Your table for 4 is
  confirmed (T-101)."), and an order or booking the owner `cancelled`. Nothing for the other
  steps. `Services.team` is the `TeamService`, for the routes.

- After a hand-off the agent keeps answering until the owner replies, so no one waits in
  silence. A phone call can't be answered this way; the owner is told to call back.
- The `by` mark is taken off before the transcript goes to the model.

### Paying first (D47–D49)

A restaurant, clinic or shop can set `pay_first`: the visitor pays online through SSLCommerz,
into the business's **own** store, before anything is placed. The shop sets it per delivery area
(`Zone.pay_first`, D92), as BD shops take the advance outside Dhaka. What's paid is the table's
`Payable.due`, and the payment moves the row to its `PAID_STATUS`:

| Desk | Pays first (`PAY_FOR`) | Then |
|---|---|---|
| Shop | the delivery charge, in an area with `pay_first` | `confirmed`, with no call: the advance is the confirmation (D92); the rest in cash on delivery |
| Restaurant | the delivery charge, for a delivery (pickup pays nothing first) | the rest on delivery |
| Clinic | a deposit the owner sets, copied onto the appointment | the clinic phones to confirm |

Nothing to pay (free delivery, a pickup) saves it `new` as before. The owner turns it on and
off, sets the amounts and connects the store on the frontend's Agent → Payments section.

**Each business's own store (D49).** `payment_accounts` holds its SSLCommerz store id,
store password and test mode; `PUT /api/agent/sslcommerz` checks them with SSLCommerz first
(it opens a ৳10 session no one pays; a 400 carries SSLCommerz's reason) and no route returns
the password. The agent asks for payment only when `pay_first` is on (a shop: in some area,
`ShopConfig.pay_first`) **and** a store is connected (`Store.pays_first`): without one, the prompt says nothing about paying, the tools
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
    ─► UPDATE … WHERE status = 'unpaid' → PAID_STATUS (new; a shop order confirmed), paid,
       payment_ref (only the first post wins)
    ─► "✅ Payment received…" added to the chat as the team's (the page and the model see it;
       the bot sends it on Telegram) and the owner's alert
```

- The agent can't mark anything paid: no tool moves `unpaid` (moving an unpaid appointment
  keeps it unpaid), and the prompt says nothing is placed until a message says so. What the
  browser posts is never trusted; only `val_id`, asked about with the business's own store.
- The link opens a fresh session each time, so it doesn't run out. The row's id is long and
  random: the link is its own key, like the Telegram webhook's channel id.
- The owner can still move `unpaid` → `new` (a shop order: `confirmed`) when it was paid some
  other way, or cancel it.

### How a shop order is confirmed (D92)

Each order is confirmed **one** way, as Bangladeshi f-commerce shops do it:

| The order | Confirmed by | It's saved | The buyer hears |
|---|---|---|---|
| Its area has `pay_first` (and a store is connected) | the advance: the delivery charge paid online | `unpaid`, then `confirmed` by the payment | "Payment received… your order is confirmed; pay the rest to the delivery man" |
| Cash on delivery, `call_to_confirm` on (the default) | the shop's call: by hand, or `calls` (D89) | `new`, then `confirmed` after the call | "The shop will call … to confirm it before sending it" |
| Cash on delivery, `call_to_confirm` off | nothing | `confirmed` at once, for the owner to send (D95) | "It's confirmed and will be sent out" |

A paid order is never queued for a call: the queue takes only `new` orders. `calls.enabled`
needs `call_to_confirm` (a 422 otherwise).

## 10. Voice (M2)

A `voice/` package turns speech into `start_turn(channel="voice")` calls and
the turn's events back into speech, with Pipecat (D18): the browser's audio over
WebRTC (`POST /api/voice/offer`), Silero VAD, Deepgram for speech-to-text
(streamed; `STT_PROVIDER=chorus` switches to Chorus, one upload per utterance
after 0.5s of silence, D39; `STT_PROVIDER=whisper` to our Whisper, D87) and Piper for
text-to-speech.

- **Bangla (D84, D86):** the server's `VOICE_LANGUAGE` (`bn`, the default, or `en`) says what
  every call hears and speaks, whatever the agent. A `bn` call asks Deepgram for Bangla
  (`language=bn`) and speaks with our Bangla voice:
  `voice/bangla_tts.py` (`BanglaTTS`) sends each sentence to `BANGLA_TTS_URL`, a small server in
  `tts_server/` (its own uv project, so torch stays out of HeyDesk's) running
  `EMTIAZZ/bangladeshi-bangla-tts-vits` (VITS, Apache 2.0, a Bangladeshi accent) on the CPU.
  `POST /speak {text}` returns the sentence as 16-bit PCM, its rate in `X-Sample-Rate`. That voice
  reads Bangla letters only, so the call's prompt asks for Bangla letters throughout
  (`BANGLA_VOICE`), and `speakable(text, "bn")` says money, times, phone numbers and codes in
  Bangla words ("৳360" → "তিনশো ষাট টাকা", "7:30 pm" → "সন্ধ্যা সাতটা বেজে ত্রিশ মিনিট",
  "O-104" → "ও একশো চার"), with num2words's Bangla and the spoken শো for its শত.
- **Bangla on a free GPU, for demos (D85):** `tts_server/kaggle_tts.ipynb` serves the same
  `POST /speak` from a Kaggle T4, with a model that sounds more natural than VITS: BanglaBox
  (`Banglabox/chatterbox-bangla-tts`, a Bangla Chatterbox) or OmniVoice (`k2-fsa/OmniVoice`,
  non-commercial). Both clone one of BanglaBox's reference voices. A Cloudflare quick tunnel gives
  the notebook a public URL. The notebook prints that URL as a `BANGLA_TTS_URL=` line for `.env`,
  and the URL changes on every run.
- **Our Whisper on a free GPU, for demos (D87):** `tts_server/kaggle_stt.ipynb` serves
  `kayees/whisper-medium-bn-en-cs-faster` (Whisper medium fine-tuned on Bangla–English mixed
  speech, faster-whisper int8) from its own Kaggle session and tunnel, or with `MODEL =
  "bengaliai"` BengaliAI's Bangla Whisper medium, converted to CTranslate2 float16 at start, to
  compare. `voice/whisper_stt.py`
  (`WhisperSTT`, shaped like Chorus) posts each utterance's 16 kHz WAV to
  `WHISPER_STT_URL/transcribe?language=<VOICE_LANGUAGE>` and gets `{"text": …}` back.

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

### Confirmation calls (D88, D89)

A shop can turn on calls (`calls.enabled`, off by default): the agent then phones each buyer
who placed an order, reads it back and asks them to confirm it. There is no phone carrier, and
none is planned: the buyer's "phone" is a web page, `/phone` (dev only), open on a real phone. It
asks for a mobile number once, then waits on `GET /api/phone/rings?number=…` (SSE; a comment
every 15 s keeps a tunnel open).

```
order saved `new`, calls on ─► a `calls` row: queued, due `wait_minutes` later
queue worker, every CALL_QUEUE_SECONDS:
  ringing for RING_SECONDS, or declined ─► missed
  fewer than MAX_LIVE_CALLS ringing or talking? the next due queued call:
     "Call now" first, then the earliest due, then the oldest
     calls off for its business, or outside its calling hours ─► it waits
     its order isn't `new` any more ─► done: skipped
     no /phone page open for its mobile ─► missed
     else ─► ringing: `ring {call, business, order}` to the page
  missed ─► tries left? queued again `retry_minutes` later : done, no answer, the owner told
Answer ─► POST /api/voice/offer?call=<id> ─► talking: a new `voice` conversation with the first
          line and `state.calling`; run_call says that line first, then goes on as any call
"yes" ─► confirm_order: Jev checks the line and the yes ─► `new` → `confirmed`, an alert
the call ends ─► done; its outcome from the order: confirmed, cancelled, or no decision
```

- **The shop's settings** (`ShopConfig.calls`, edited like the rest of the config):
  `enabled`, `wait_minutes` (1), `start` and `end` (10:00–20:00, the business's time), `tries`
  (2), `retry_minutes` (30), and `opening`, the call's first line, with `{code}` and `{total}`
  filled in by Python: "Hello, this is {business_name} calling about your order {code}, total
  {total}, paid in cash on delivery. Shall we confirm it and send it out?" The Bangla seed has it
  in Bangla letters. It is the read-back `confirm_order` checks.
- **The queue** is the `calls` table: `status` queued → ringing → talking → done; `outcome`
  confirmed, cancelled, no_answer, no_decision, skipped (the order moved on first) or stopped
  (the owner cancelled the call); `tries`, `next_try_at`, `priority` (1 after "Call now"). An
  order has at most one call that isn't done. `MAX_LIVE_CALLS` (1) is for the whole server: the
  Bangla voice speaks one sentence at a time.
- **On the call** the shop's note (prompt-rules §7) says what the call is for. `cancel_order`,
  `change_order` and `track_order` with no code mean the order the call is about. After
  `confirm_order` or `cancel_order` succeeds, the call ends itself once the goodbye is spoken.
- **The owner** sees the queue on the Calls page and the call on each order: `GET /api/calls`
  (`view=waiting`, in queue order with each one's place; `view=done`, newest first; or
  `order_id=`), `POST /api/calls {order_id}` to call a `new` order now (409 while calls are off or
  it has a call waiting), `POST /api/calls/{id}/now` to move a queued call to the front, and
  `DELETE /api/calls/{id}` to stop one that is queued.
- **The phone page's rings** are kept in memory: a page per mobile, the newest wins. A ring lost
  in a restart is a missed call after `RING_SECONDS`.

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
| Searching a long menu or doctor list; a shop's search that knows Banglish ("lal" for Red) or meanings | The whole list is in the prompt, every turn; fine for the few dozen a small business has (D41). A shop over `LONG_LIST` (60) products, such as a linked WooCommerce store, lists only its groups, and `find_product` matches plain words (D80) |
| A web or voice buyer's last order | Only Telegram and Messenger know who the buyer is (the chat's `contact`), so only they get `# Their last order` (D81); a web conversation doesn't keep the browser's visitor token |
| Several tool calls in one Gigalogy reply | The first `{"tool": …}` runs; the model sees its result and can call the next |
| Progress-line policy (thresholds, rotation) | Web shows the tool's label; voice says the tool's filler |
| What the caller *heard* after a barge-in | The turn's trace says `interrupted`; the text is what was generated |
| Clearing old `turns`, a price for Gigalogy, the cost of Jev, Gemini embeddings and speech-to-text | Every turn is kept; a turn's cost is Groq's alone (Gigalogy reports tokens, no price), so a Gigalogy turn's cost is 0, meaning unknown (D71) |
| Usage shown to the owner, limits per plan, Cloudflare Turnstile | One `VISITOR_DAILY_TURNS` and `WORKSPACE_DAILY_TURNS` for every business (D51); cost is in each turn's log line. Clearing the browser's token makes a new web visitor, still inside the business's daily turns and the IP's rate limit (D52) |
| Real bookings (a free slot, a calendar) | The clinic takes an appointment *request*; the clinic phones to confirm |
| Restaurant: finding an order by a phone written another way (+880 for 0), changing an order's dishes | The phone must match as it was given; to change the dishes, cancel and order again (D65) |
| Restaurant: telling a visitor who called (voice), an SMS when nobody is on the chat | A call can't get the line; the owner phones them. Web, Telegram and Messenger only (D66) |
| Restaurant bookings: real tables and their layout, deposits, a waitlist, a booking that must end before closing, a list by day, two visitors taking the last seats in the same second | One shared count of `seats`; a booking's start is inside the hours; no money is taken; a full slot offers other times; the owner filters the list on the frontend; the second of two at once could overbook by a table (D64) |
| Bangla voice: a GPU, streaming the audio inside a sentence, several calls speaking at once, English words in a Bangla reply, Bangla on Chorus | One sentence at a time, whole, on the CPU or a Kaggle T4 for demos (D85); a second call waits for the first's sentence. The local VITS voice drops Latin letters, so the prompt asks for them in Bangla letters. Chorus is sent `bn`, untested (D84) |
| Teams: more than one owner, other roles or per-page permissions, one email in two businesses, handing the business to someone else, the owner seeing an admin's changes, a 2FA | One owner and up to 10 moderators per business, one fixed split (D97); an invited email can't already have an account; the activity has the routes, not what changed in them |
| Billing, phone numbers, hosting | No plans or billing; voice in the browser only (a confirmation call rings a `/phone` page, D88, D89); a Docker image and `compose.yaml` run it on one machine (SQLite in a volume), and no host is chosen |
| Telegram: voice notes, messages over 4,096 characters, buttons on the web chat, buttons the model chooses, a delivery-area button, sharing the phone with a tap | Text, photos (D63) and Python's buttons (D99), and replies are a few sentences (D42). The district comes from the address (D98), so there's no Inside/Outside Dhaka button; Telegram's contact button and Messenger's phone quick reply are a later step (docs/product-gaps.md §2) |
| Owner takeover: a chat the owner never gives back with `/done` | It stays with the team until it's quiet for `CONVERSATION_IDLE_HOURS`; then a new conversation starts with the agent (D43) |
| Telegram: a repeated update (Telegram retries when we don't answer) | The webhook says OK before the turn runs, so a retry is rare; one would be answered twice |
| Ending logins when a password changes | A login token lasts 7 days, and a new password (changed, or reset with the emailed code, D93) doesn't end the ones already made. An admin's role is checked against the database on every request (D23) |
| Changing `SECRETS_KEY` | It's set once; the secrets saved with the old key can't be read with a new one (D74) |
| Shop: Instagram, WhatsApp | Web chat, voice, Telegram and Messenger for now (D45, D60) |
| Messenger: Facebook Login to connect a Page, the Get Started button, a reply after the 24-hour window, the Page's own replies from its inbox | The owner pastes a Page access token made in our Meta app; a visitor's first message starts the chat; text, photos (D62) and Python's buttons (D99). A reply after 24 hours fails and is logged. The agent doesn't see what a person wrote in the Page's inbox (D60) |
| Photos: several in one message, a photo sent as a file on Telegram, a photo with no product to match it (the restaurant, the clinic), deleting a photo's file when the owner removes it | Web chat, Messenger (D62) and Telegram (D63), one photo a message, the shop only; a removed photo's file and vector stay, and search skips it (D57) |
| Photos: who can see one | A photo's URL is public but has a random id, like the chat link; a visitor's photo is kept for the owner's view (D57) |
| Import: saving a business's column match, one column split into two fields ("Size/Colour"), a limit on suggest calls, Shopify or a POS | The screen asks again on each upload (the AI's suggestion makes that quick); the owner splits the column in the sheet; suggest is for logged-in owners only, one model call a click; CSV only for now (D53) |
| Linked sheet: writing the stock back to the sheet, a private sheet (a Google service account), checking the stock again just before an order is saved, Excel on OneDrive, a sync racing an order | The sheet is read only; the owner updates a count when they restock or recount, and an order packed but not yet marked shipped is taken off once more (mark it shipped first). Shared by link, so the owner keeps private columns elsewhere. The list is at most `SHEET_SYNC_MINUTES` old. Sheets only. A sync that writes while an order takes stock could lose that order's take; the same-text check makes a write rare (D58) |
| WooCommerce: reading a webhook's body (it only starts a sync), a store on plain HTTP or with plain permalinks, reading back a change made to our order in Woo's admin, a returned order's stock, product photos from Woo, attributes other than size and colour as variants, a store with thousands of products, Shopify | A webhook starts a whole sync, which is simple and catches everything; HTTPS with `/wp-json/` only, which nearly every store has; HeyDesk is where our orders move; a returned order is left as it is in Woo and the owner restocks there; the owner adds photos in HeyDesk; other attributes are a line in the description; a sync is one call per 100 products plus one per variable product; Woo first (D75) |
| Website chat: only the business's own websites may show its bubble, removing a logo's file | Anyone can paste a business's line, as anyone can share its chat link; the daily limits (D51) and rate limits (D52) keep the cost small. A replaced logo's file stays (D82) |
| Confirmation calls: a phone carrier, who may open `/phone` for a number, a call per business at once, restaurant and clinic calls | No carrier is planned: the page on a phone is the phone. Dev only, like the Call button: anyone who opens `/phone` with a mobile gets that mobile's calls, which say the order's code and total. One call at a time for the whole server. The shop only (D88, D89) |
| Payments: refunds, a restaurant's charge per area, an unpaid order that expires | The owner refunds in SSLCommerz's panel (a payment for a cancelled one is an alert saying so); one restaurant delivery charge to any address; the owner cancels what was never paid |
