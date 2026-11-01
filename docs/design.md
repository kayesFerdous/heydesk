# HeyDesk — design

How the MVP works. One page per idea, no more. [PLAN.md](../PLAN.md) says what
to build when; this file says how. Decisions are in [decisions.md](decisions.md).

**Keep it simple.** Build the normal path well. An edge case gets code only
when it's cheap or it would break the demo; otherwise it goes in §12 *Left out*.

---

## 1. What it is

An online shop fills in its details (products, delivery areas, FAQ) and adds its own notes, and it
has an AI front-desk agent. The shop is a **desk** with its own prompt, tools and tables (D41); it
is the only one now (D164). Visitors talk to it by **web chat** in the
browser, or on **Telegram** with the business's own bot (D42), or on its Facebook Page's **Messenger** (D60), or on its **WhatsApp** number (D103). It answers questions, takes orders and messages, and hands over to a
person when needed. The business sees orders and messages through the API, which
the separate frontend (`../heydesk-frontend`) uses.

**The model talks; code guarantees.** The model writes the words. Python
decides prices, totals, times, what to ask next and when something may be
saved. Nothing is saved without reading it back and hearing a yes.

## 2. Words

| Word | Meaning |
|---|---|
| Desk | A kind of business (`shop`, the only one, D164): one folder in `desks/` with its own config fields, prompt, tools, tables and owner routes (D41) |
| Agent | A business's agent: one desk's config, which the business edits. Edits are live on the next message |
| Store | A business's data as its desk sees it (`ShopStore`: products, orders, messages), always scoped to one workspace |
| Conversation | One visitor's chat or call: its `messages` are the whole transcript |
| Turn | One visitor message and everything the agent did for it |
| Channel | Where a visitor talks: `web`, `voice` or a messaging app (`telegram`, `messenger`, `whatsapp`). A messaging app's link to the business is a row in `channels` (D42, D60, D103) |
| Tool | Something the model can call: `update_cart`, `place_order`… A desk's own, plus the shared `take_message`, `handoff_to_human`, `off_topic` and `search_knowledge` |
| Note | A desk's line from the conversation state (the shop's order so far), added to the system prompt before every model call |

## 3. The rules

1. **Python does facts.** Prices, totals, dates, times, availability: decided in
   tools, never by the model.
2. **Read back, then a yes, then save.** Writing tools run the commit check
   (§7). It fails closed: with no read-back of exactly these details, nothing is saved.
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
├── ports/               Protocols: LanguageModel, Embedder, KnowledgeStore, PhotoIndex,
│                        MediaStore, TelegramApi, MessengerApi, WhatsAppApi, PaymentGateway, the sheet reader, WooApi
├── adapters/            one per port, each with a fake: GigalogyLLM (default) and GroqLLM,
│                        Gemini, Qdrant (documents and photos), the Telegram Bot API, Messenger's
│                        Graph API, WhatsApp's Cloud API (D103), SSLCommerz, bKash (D109), Google Sheets' CSV (D58), WooCommerce's REST API (D75),
│                        photo files on the disk or in Cloudflare R2 (D101)
├── runtime/             the engine: loop, tool contract, commit check, parsers, prompt, speech, cards
├── desks/               one folder per kind of business (D41)
│   ├── base.py          Desk (what a desk is) and Store (its data: messages, documents search, its list replaced whole)
│   ├── shared.py        take_message, handoff_to_human, off_topic, search_knowledge
│   ├── shop/            __init__ (config, prompt) · tools · values · models · store · api
│   └── csv_import.py    reading a CSV file into a desk's list; matching its columns to ours (D50, D53, D115)
├── models/              the shared tables; `customer_request.py` is what orders and messages share
├── services/            agents, conversations, users, admin, visitors (the limits, D51), rate_limits (D52),
│                        knowledge, photos (D57), sheets (D58), woo (D75), channels (Telegram,
│                        Messenger and WhatsApp, D42, D60, D103), team (the owner's alerts, D43), payments (D47)
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
| `tools.py` | Its tools: the shop's `find_product` (D80), `update_cart`, `place_order`, `track_order`, `change_order`, `cancel_order`, `request_exchange`, `show_photos` |
| `values.py` | Its shapes, no database: `Product`, `Zone`, `match_zone()`, `ShopConfig`, `mobile()`; `districts.py`, Bangladesh's 64 districts and their old spellings (D98) |
| `models.py` | Its tables: `products`, `coupons` and `shop_orders` |
| `store.py` | Its data access, for its tools and its owner routes, always one workspace |
| `api.py` | Its owner routes: `/api/products`, `/api/shop/…` |

`desks/__init__.py` lists them (`DESKS`), and `tools_for(desk, config, has_documents)` is a desk's
tools for this business (`Desk.own_tools(config)`) then the shared ones: `take_message`, `handoff_to_human` and `off_topic` (D51) always,
`search_knowledge` when the business has documents (D35). To add a kind of business: a new folder, and a line in `DESKS`.

**The config.** `runtime/config.py`'s `AgentConfig` has what every business has: name, business
name, timezone, currency, greeting, address, `hours`, the owner's **notes**, FAQ, hand-off. A desk
subclasses it: the shop adds `delivery` (its areas, each with a charge, usual days, its `districts` (of the 64, in English or Bangla, saved in English: Chittagong or চট্টগ্রাম → Chattogram), D98, and `pay_first`, D92; an area with no districts is everywhere else (D100), and there can be one, a 422 otherwise), `call_to_confirm` (default on: a cash-on-delivery order waits for the shop's call; off: it's confirmed when placed; `calls.enabled` needs it, D92), `free_delivery_over`, `policies`, `size_guide` (D54) and `exchange_days` (D55); it sets its own default greeting. Every agent also has a `widget`: how the chat looks on the business's own website (D82): `colour`, `side`, `nudge` (the line that pops out of the bubble; empty: none), up to 4 `starters` (tap-to-ask buttons, each desk with its own) and `logo` (only one saved by `POST /api/agent/logo`). A field the desk doesn't have is a 422, so
there's no role text to overwrite: it's the desk's code. Only the fields actually given are saved,
so a default (a greeting) can change later. One model serves every agent (`MODEL` in `.env`).

**Setup is one call and a list** (D26). `POST /api/agent` needs only `{"kind": "shop"}`:
the business name comes from signup. `GET /api/agent` then says what's `missing`, in plain words
("Add your products.", "Add your delivery areas and charges."); it's empty when the agent is set up. There's no publish
step (D15): the agent answers from the start, and what's not filled in is simply not in its prompt.

**The products are a table, and they're in the prompt.** `PUT /api/products` replaces them
whole. The prompt lists them, one line each, up to `LONG_LIST` (D80).

**One agent per business** (D22). `agents.workspace_id` is unique and `agents.kind` names its
desk, so a business's agent is just "the agent": no ids in the URLs. Creating a second is a 409.
A desk's routes answer 404 for an agent of another kind.

**`AgentService`** (`services/agents.py`): `create`, `update` (validates; live from the next
message), `get` (with `missing`), `by_key` (a `LiveAgent`: its desk, config and document titles),
`store(ws, kind)` for the owner routes. There is no publish step and no versions (D15).

**Dev agent.** In development, startup runs `ensure_dev_agent()`: the `dev` workspace and the
agent `dev-<kind>` from `DEV_AGENT_KIND` + `DEV_AGENT_SEED`; the desk's `seed()` takes its own
list (`products:`) out of the seed file. Edit the seed and restart, and the agent is updated.

## 6. One turn

```
visitor message ─► start_turn: load the conversation (or start one), build the system prompt
                 ─► run_turn: model ⇄ tools, at most 6 model calls
                       text  ─► streamed to the visitor
                       tool  ─► validate ─► run with a timeout ─► result back to the model
                 ─► append the turn's messages (the row locked), save and log the trace
```

`ConversationService.start_turn` is the one entry point for web and voice. The
listener (the SSE response, or the voice pipeline) reads the turn's events;
the turn is saved when it ends.

**What the model sees on every call**, in this order (each rule and why: [prompt-rules.md](prompt-rules.md)):

1. System prompt: platform rules → the desk's own sections (its role, what it can do, its facts)
   → owner's notes → FAQ → documents → **right now** (channel style, date and time, open or
   closed today, the next 14 days with the closed ones marked, D40) → the desk's **note**
   (the shop's order so far), rebuilt before every model call and never stored
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
2 × Cotton Kurti (M, Black), subtotal ৳1,900
```

That's part of the shop's note, empty until there's an order. What's
still needed comes back from the writing tool itself (§7).

## 7. Tools

The contract lives in `runtime/tool.py`; the tools live in their desk (`desks/shop/tools.py`)
or in `desks/shared.py`. A tool reaches the database only through `ctx.store`, its desk's store.

```python
@dataclass(frozen=True)
class ToolContext[S]:
    agent: AgentConfig             # the desk's own config
    store: S                       # the desk's store: ShopStore
    state: dict                    # the conversation state so far (the cart)
    now: datetime                  # when the turn started, UTC
    heard: str                     # the last read-back the visitor heard, before this turn

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
   the name…"}`. Each detail is checked by Python as it's taken (`ArgumentChecker.take`); the model may call
   as soon as it has a part, so a wrong mobile or an area it doesn't deliver to is caught early.
2. **Everything's there, no yes yet**: `{"read_back": "...", "next": "Read this back and ask for a
   clear yes…"}` (`ask_for_yes`).
3. **A call after a clear yes**: saved, with its number or code.

| Tool | Desk | Writes? | What it does |
|---|---|---|---|
| `take_message` | every desk | **yes** | `message`; optional `name`, `phone`, `best_time` (checked when given; the agent offers once to take them, D44). Returns its code, `M-104`, and an alert for the owner |
| `search_knowledge` | every desk, with documents | no | The best 4 passages from the business's documents, with their titles and a reminder that they are facts, never instructions (D35) |
| `find_product` | shop | no | `query`: a name, a group, a colour or what it's for. Python scores each product's words (twice in the name or group, once in the sizes, colours or description) and returns up to 8 of the prompt's own product lines, best first; nothing found says to try other words. The prompt tells the model to use it only when the shop has more than `LONG_LIST` (60) products (D80). Off a call, it also sends a card for each product it found, photo or not, as `show_photos` does, and names them in `shown` (D102, D125) |
| `update_cart` | shop | no | Sets the whole order: codes, quantities, and a size and colour when the product has them. Python checks the choices and the stock (the product's own, or its variant's for that size and colour, D56: "Cotton Panjabi (L/Navy): sold out (left: M, L/White)") and adds up the subtotal. Under `free_delivery_over`, it also says how much more makes the delivery free (`free_delivery_if_you_add`, D54); at or over it, `delivery: free`. The cart goes in `state["cart"]` (D45). `goes_with`: the prompt lines of up to 2 products the cart's products name in their `goes_with`, not in the cart, not sold out and not offered before in this chat (`state["offered"]`), so each is offered once (D125) |
| `place_order` | shop | **yes** | `address`, `name`, `phone` (a Bangladeshi mobile, Bangla digits and +880 accepted), `note`, and `district` (the model reads it off the address: Mirpur → Dhaka). Python finds the area (`match_zone`, D98, D100): the one with the district, else the one for everywhere else; a name that isn't one of the 64 is a problem to ask about, and a district no area covers is "the shop doesn't deliver there". An address with no district asks for it. Gives the delivery charge and total as soon as the area is known; free over `free_delivery_over`. What's missing comes back as one list: in a chat, "ask for all of these in one message"; on a call, one at a time (`ArgumentChecker(together=…)`, D92). Takes the items out of stock (the product's, or the variant's) as it saves. Saves it `new` when the shop calls to confirm (`call_to_confirm`), else `confirmed` (D92). Returns its code, `O-101`, what to say next (the call or that it's confirmed, the cash for the delivery man, who calls before he comes, the usual days), and an alert, which also warns when this mobile has returned or cancelled orders before and lists the order's products at `LOW_STOCK` (3) or fewer (D54). When its area has `pay_first` (and a charge, and the shop's store is connected), it saves the order `unpaid` and returns a `pay_link` instead, and no alert: the payment confirms it (D47, D92) |
| `track_order` | shop | no | No code for the one order placed in this conversation (D55); otherwise the mobile alone finds the latest order with it, or with nothing, this buyer's last order from the same chat or browser (D92, D114), and the code and the mobile find any: its status in words, items, total, what was paid online and what's left for cash on delivery, how long delivery usually takes, and the `pay_link` while it's unpaid; while shipped, the courier's last words (`courier_says`, D112). A wrong code and a wrong number get the same answer |
| `change_order` | shop | **yes** | Found like `track_order`, except the mobile alone: a change needs the code (or the order from this chat, or this buyer's last). Then any of new `items` (the whole list, checked and priced as `update_cart`'s, with the order's own items counted as on the shelf, D92), `address` (with its `district`, or it asks for it, D98), `new_phone`, `note`, only while `new` or `confirmed` (`CHANGEABLE`). New items or a new district work out the delivery charge (free or not) and total again; not once its charge was paid online. The store swaps the stock in one transaction. Reads back only what changes; alerts the owner (D55) |
| `cancel_order` | shop | **yes** | Found like `change_order`; only while `unpaid`, `new` or `confirmed` (not once the courier has it). The items go back in stock |
| `request_exchange` | shop | **yes** | Found like `change_order` (the mobile alone isn't enough: the code, or the order from this chat, or this buyer's last); only a `delivered` order, within `exchange_days` of being marked delivered. `product` (a code from the order) and the `size` and/or `colour` wanted, checked against the product's own (`Product.choose`, as `update_cart`) and refused when that variant is sold out, optional `reason`. Saved as a message for the team (`M-104`) with an alert: the owner arranges the swap; no stock changes (D55) |
| `show_photos` | shop | no | `products`: up to 10 codes. Sends a `photos` event with each one's card (name, price, first photo or none, first video or none), and says which were shown; a product with no photo is named in `no_photo` so the model describes it, and one with a video in `video`. Not on a call: there it says photos can't be shown here. Each channel shows the cards its way (D125), each with an Order button (D99) (D57) |
| `handoff_to_human` | every desk | no | Marks the conversation `needs_human` and alerts the owner (D43), returns the hand-off message and phone. Optional `unanswered`: their question, when it hands off for not knowing; saved as a missed question (D143) |
| `flag_complaint` | every desk | no | `reason`. A hand-off (D43) that opens a ticket for the team, one per chat while open, and alerts "😠 Complaint C-101: reason" once; no yes is asked, as it's the team's to-do (D138) |
| `take_lead` | every desk | **yes** | `when` (their words with the clock time, read by `parse.day_and_time`: the next 14 days, inside the hours), `phone`, optional `note` and `name`. Read back "We call you on Fri 2 Oct, 5:00 pm at …"; after the yes a lead `L-101` with `call_at` in UTC, and an alert "📞 Call back L-101". The team taps how it went (D139) |
| `off_topic` | every desk | no | Called instead of answering a message that has nothing to do with the business. Its `reply`, one short line in the visitor's language, is sent as it is and ends the turn: no second model call. Each call is a strike for the visitor (D51) |
| `remember` | every desk | no | `about` (a word or two) and `note`: something the visitor said on their own (a taste, a size, who they buy for, an allergy). Kept on their `visitors` row, replacing the note on the same `about`, newest 20; never asked for (D124) |

**The commit check** (`runtime/guards.py`, D107): a writing tool's first call returns its
read-back (`ask_for_yes`), which goes into the conversation state as `read_back`. The talking
model judges the visitor's reply: only after a clear yes does it call the tool again. At the
start of each turn the loop puts the state's `read_back` in `ToolContext.heard`; `confirm`
makes a `Confirmed[T]` only when this call's read-back equals it. So the visitor heard exactly
what is saved, on an earlier turn: a call in the same turn as its read-back, or with any detail
changed, only reads back again. A store's writing methods accept only `Confirmed[...]`, so mypy
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
- **What it couldn't answer (D143).** `search_knowledge` finding nothing, or `handoff_to_human`
  with `unanswered`, saves the question (`missed_questions`). `GET /api/agent/missed` groups the
  untaught ones by likeness (`group_alike`: their words, compared with `difflib`, 0.75 alike;
  no model, so no cost), the most asked first. *Teach it* (`POST /api/agent/missed/teach {ids,
  question, answer}`) adds "question, then answer" as a knowledge entry and takes the group off
  the list, so the next time `search_knowledge` finds it (a business with no documents gets the
  tool from its first).

**AI off for all (D165).** `ai_off` on the config is the owner's master switch, in the agent's
settings: `PATCH /api/agent {ai_off: true}`. Every visitor on
every channel is treated as having the AI off, so a message waits for the team exactly as with AI off for one visitor (I2), the
web chat and calls included, and comments on posts get no reply. Turning it off again lets the
agent answer from the next message.

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
  removed) is skipped. A product a list gives no photo links keeps its photos by its code.
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

**Photos from a list (D102).** A CSV's or a sheet's `photos` column (up to 3 links, apart by
spaces, commas, `;` or `|`; the rows of one product add theirs up), and a WooCommerce product's
`images`, are links. `ShopStore.with_photos` turns them into the shop's own photos before the
list is saved, at an import, a sheet's sync and a Woo sync:

```
a link ─► its key: <workspace_id>/products/<first 32 hex of its SHA-256>.jpg
       ─► in the media store already? that's the photo (the next sync costs a look-up)
       ─► else fetch_image(link) ─► the same path as an upload (JPEG, the store, Gemini, Qdrant)
```

- **A product with links gets those photos; one with none keeps its own** (the owner's
  uploads). A link that fails is left out and tried again at the next sync that reads it; the
  rest go on. Four links at a time.
- **Only public addresses are fetched** (`fetch_image`): http(s), every address of the host
  public (no localhost, 10.x, 169.254.x…), at most 10 MB, 15 s, 3 redirects, each checked. In
  development localhost too, for the test shop (D76). A Google Drive share link
  (`/file/d/<id>/view`) becomes its download link.
- **The first import waits.** An import or a sheet's linking with many new links takes as long
  as they do (a second or so each, four at once). Later syncs only look them up.
- **A search shows them.** `find_product` sends a card for each product it found, and the
  prompt says to show up to 10 that fit with `show_photos` when a visitor asks about a product
  or looks for a kind, unless `find_product` showed them.

**Product cards side by side (D125).** A card is `{code, name, price, url, video}`: `url` is
the first photo or "", `video` the first video link or "". Each app shows a row of them its way:

| Channel | The cards |
|---|---|
| Web chat | one row that scrolls sideways; a card with no photo has a blank square; *Order this*, *Details*, and *Video* when it has one |
| Messenger | one carousel of up to 10 (photo optional); buttons *Order this* (postback), *Details* and *▶ Video* (links) |
| Telegram | the first 3 with a photo, a photo each (*Order this*, *Details*, *▶ Video*); the rest one "More to order:" message with a button each, "🛒 Name · price" |
| WhatsApp | the first 3 with a photo, a photo each (*Order this*, the page and video links in the caption); the rest one list, a row each (name, price), whose tap is its "Order [code]" |

- **Prices come from Python** (`money()`), as in every card. A tap is only words (D99).
- **Products have `videos`** (up to 3 http(s) links: YouTube, a Facebook video, a how-to) and
  **`goes_with`** (up to 3 codes). Both come from the Products page, a CSV or a sheet (columns
  `videos` and `goes_with`); Woo has neither. An import whose product has none keeps the ones
  it had, as photos are kept (`KEPT`). The product page links each video; nothing is embedded,
  so the page's policy stays `default-src 'none'`.
- A `goes_with` code that isn't in the shop is skipped, not refused, so a deleted product
  doesn't block saving the list.

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
| `workspaces` | id, name, status (`active`/`suspended`), suspended_reason (admins only, D70), plan (`trial`/`starter`/`growth`/`pro`) and paid_until (D106) |
| `users` | workspace_id (empty for an admin), role (`owner`/`moderator`/`admin`, D97), email, password_hash, email_verified_at (empty until the emailed code is typed, D93; for a moderator, until they join), telegram_chat_id (their own chat with the bot, for their alerts, D148), available (new hand-offs may go to them, D149) |
| `audit_events` | actor_id, workspace_id, action, target, at: what an admin did in a business. Insert-only |
| `team_events` | workspace_id, actor_id and actor_email (no foreign key: a removed moderator's changes stay), action, target, at: each write an owner or moderator made that succeeded, for the owner (D97). Insert-only |
| `turns` | workspace_id, conversation_id, channel, llm, at, total_ms, first_output_ms, input/output tokens, cached_tokens (D154), cost_micros, tools, error, handed_off, interrupted: each agent turn's trace, for the admin's sums (D71). No one's words. Insert-only |
| `agents` | kind (its desk), name, public_key (unique), config JSON |
| `conversations` | agent_id, channel, contact (a messaging app's chat id, D42; the browser's token on the web chat and a call, D114), name (the visitor's name from Telegram or WhatsApp, D104), status (`active`/`needs_human`/`human`: the team is answering, D43), assigned_to (the team member who has it, or empty; a removed one's go back to empty, D126), done_at and snoozed_until (D128), taken_over_at (the team's last word while it took the chat over, D135), labels JSON (the team's own words for it, D136), history_from (where the agent's memory starts in messages, D157), nudged (the one nudge for an unfinished order went, D139), state JSON, messages JSON |
| `tickets` | the shared columns, reason (the agent's words for what's wrong). Code `C-101`; open ↔ solved; one open ticket per chat; who has it is the chat's `assigned_to` (D138) |
| `leads` | the shared columns, call_at (UTC), note (what they want), why (a lost one's reason, lower case). Code `L-101`; open → sold / not_now / lost, and back to open (D139) |
| `post_comments` | post_id, commenter (their id in the comment), comment_id (the one answered), conversation_id (the chat it opened), nudged (the one more reply went, D142); unique per business, post and commenter: one public and one private reply each (D141) |
| `page_posts` | post_id (unique per business), text, products JSON (codes, in order), tagged (the owner set them): what a post is about, found at its first comment and used by every later one (D142) |
| `missed_questions` | conversation_id, question (the model's few words), created_at, taught_at (empty until *Teach it*) (D143) |
| `notes` | conversation_id, author (the team member's email), text, created_at: the team's notes on a chat, insert only, never in the transcript (D136) |
| `visitors` | key (`web:<the page's token>`, `telegram:<chat id>`; unique per business), day (the business's local day), turns (on that day), strikes (off-topic turns in a row), blocked_why (`team`, `flooding` or `off_topic`; empty: not blocked), blocked_until (empty while blocked: until someone unblocks them), blocked_by (the email of who pressed Block), told_blocked (the one notice went), ai_off (the team answers them by hand) (D51, D127), notes (`{about: note}`: what the agent remembers about them, D124) |
| `channels` | agent_id, kind (`telegram`/`messenger`/`whatsapp`), token (a bot's, a Page access token, or a system user's token; encrypted, D74), secret (Telegram's; empty on Messenger and WhatsApp), address (the bot's username, the Page's id, or the number's id), owner_chat_id (where alerts go, D43; Telegram only). One per agent and kind (D42, D60, D103) |
| `sheet_links` | agent_id (unique), url, mapping JSON (our field → their column), seen JSON (each product code's stock cell as last read), checked (SHA-256 of the last text fetched), synced_at, problem (empty: fine). One per agent (D58) |
| `courier_links` | agent_id (unique), kind (`steadfast` or `pathao`), key, secret and password (encrypted, D74), username, store_id (Pathao's pick-up store), sandbox, problem (empty: fine). One per agent, shops only (D90, D113) |
| `woo_links` | agent_id (unique), url (the store's `https://` address), key, secret (encrypted, D74), ids JSON (each product code's Woo product and variation ids), checked (SHA-256 of the last products read), synced_at, problem (empty: fine). One per agent, shops only; not with a sheet (D75) |
| `subscription_payments` | plan, months (1 or 12), amount, status (`unpaid`/`paid`), payment_ref, paid_at: a business paying HeyDesk for a plan, through our own SSLCommerz store or bKash account (D106, D109) |
| `payment_accounts` | provider (`sslcommerz`/`bkash`), store_id, app_key, app_secret and password (both encrypted, D74), username, sandbox: the business's own SSLCommerz store or bKash account, one of each per business (D49, D109). No route returns a secret |
| `documents` | agent_id, title, filename, kind (`text`/`markdown`/`pdf`), text, chunk_count (D34). Its chunks are in Qdrant, not here |
| `team_messages` | the shared columns, body, best_time. Code `M-104`; new ↔ handled |
| `products` | code (unique per business), name, description, price, was_price (before a sale; empty: not on sale, D54), category, sizes JSON, colours JSON, stock (empty: not counted), variants JSON (`{size, colour, stock}` per size and colour; an empty size or colour means any; when there are any they hold the stock and `stock` is empty; a size and colour none matches isn't counted, D56), photos JSON (up to 3 photo URLs, the media store's url + `/<workspace_id>/products/<id>.jpg`; their vectors are in Qdrant, D57, D101), position |
| `coupons` | code (unique per business, in capitals), kind (`percent`/`amount`/`free_delivery`), value (a percent or minor units), ends_at, max_uses (empty: no limit), used (D133), only_for (an offer's chats, JSON; empty: anyone, D140) |
| `shop_orders` | the shared columns, the paid columns, lines JSON, subtotal, delivery_charge, discount and coupon (a coupon's code and what it took off the items, D133), total (subtotal − discount + delivery_charge), zone, address, note, woo_id and woo_sent (the order in a linked WooCommerce store, and our status as last written there; "" never, D75), parcel_id and courier_status (booked with the courier and its last words; "" not booked, D112), paid_in (the rider's cash reached the shop) and reason (why it came back or was cancelled), set once it ended (D131), source (where it was placed: `chat`, `page` for *Buy now* or `team`, D144). Code `O-101`; unpaid → confirmed (the delivery charge was paid, which confirms it; or the owner moves it, D92); new → confirmed (the shop called the buyer); placed `confirmed` when the shop doesn't call (D92), or when the team typed it (D131); confirmed → packed → shipped → delivered or returned (confirmed → shipped still works); new, confirmed or packed → on_hold → confirmed; anything before shipped → cancelled |

The **shared columns** (`models/customer_request.py`) are what the agent took for the business: conversation_id,
number (101, 102… per business and table), status, name, phone, created_at, updated_at. A status
change the table's `FLOW` doesn't allow is a 409 `invalid_transition`. The **paid columns**
(`Payable`, same file) are on what a visitor can be asked to pay for first (D47, D48): currency,
paid (minor units, online), payment_ref (the gateway's id).

**Writes at the same moment (D162).** Production runs on Postgres, where two transactions can read
the same row before either writes. Three kinds of write lock a row first, with `SELECT … FOR UPDATE`;
SQLite ignores it, as it writes one transaction at a time anyway:
- **A business's numbers and stock:** `Store._lock` locks the business's `workspaces` row. Every
  save that takes a number (`add`, an order) and every write to the stock (an order placed, changed
  or cancelled, a coupon used, the list replaced, a sheet or Woo sync) takes it first, so two at
  once take turns: each gets its own number, and the last one goes to one buyer.
- **A conversation's transcript:** whatever adds to `messages` (a turn saved, a team reply, the
  Page's own words, a payment's news) reads the row with `lock=True`, so neither drops the other's
  line (rule 5).
- **A visitor's row:** their first two messages at once both try to make it; the unique
  (workspace, key) refuses the second, which goes on with the first's. Counting a turn locks it.

Money is in minor units (paisa). Datetimes are stored in UTC; "today" is the
business's timezone. JSON columns: assign a new value to change one, because
SQLAlchemy doesn't see in-place edits.

## 9. API and streaming

| Method | Path | Does |
|---|---|---|
| GET | `/` | Dev chat page (development only) |
| GET | `/api/chat/{key}` | Agent name, greeting, `photos` (true when it takes photos, D57), `voice` (true when it takes calls, so the page shows its Call button, D108) and `widget` (the website chat's look, its nudge and starters filled in like the greeting, D82) |
| POST | `/api/chat/{key}` | `{message, conversation_id?, visitor_id, photo?}` → Server-Sent Events; `photo` is a JPEG or PNG in base64 (D57) |
| GET | `/api/chat/{key}/{conversation_id}/team` | What the team wrote from Telegram; the chat page asks every few seconds (D43) |
| POST | `/api/auth/signup` | A new business and its owner → 204, and an email with a 6-digit code; no token until it's typed (D93, D94) |
| POST | `/api/auth/login` | → `{token}`; a 401 for a wrong email or password; a 403 `email_not_verified` for the right one before the code is typed (D94) |
| POST | `/api/auth/verify` | `{email, code}`: the code from the signup email → `{token}`, the first login; a 403 if it's wrong or expired |
| POST | `/api/auth/verify/resend` | `{email}` → 204, always: another email with the code if the account exists and isn't verified |
| POST | `/api/auth/forgot` | `{email}` → 204, always: an email with a reset code if the account exists (D93) |
| POST | `/api/auth/reset` | `{email, code, new_password}` → `{token}`; a 403 if the code is wrong, expired or used |
| GET | `/api/auth/me` | Who you are: email, role (`owner`, `moderator` or `admin`), your business, `available`, and your Telegram alerts link (`telegram_link`, `telegram_linked`, D148) |
| PUT | `/api/auth/password` | Your own password: `{current_password, new_password}` → 204; a 403 if the current one is wrong (D68) |
| GET/POST/DELETE | `/api/team/members` | The owner's team (D97): everyone in the business (`active`, or `invited` until they join); add a moderator by `{email}` → 201, and an email with a code; `POST …/{id}/invite` sends it again; `DELETE …/{id}` removes one, out at once. A 409 for an email with an account, or past 10 moderators |
| PUT | `/api/team/members/{id}/available` | `{available}`: the owner switches a member on or off for new hand-offs (D149) |
| GET | `/api/team/stats?days=7` | Each member's open chats, replies, chats replied in, tickets solved and leads sold (D150) |
| PUT | `/api/auth/me/available` | `{available}`: switch yourself on or off for new hand-offs (D149) |
| GET | `/api/team/activity` | What the owners and moderators changed, newest first, paged (D97) |
| GET/PATCH/POST | `/api/admin/…` | Admins only (404 for owners): businesses (suspend), users (reset an owner's password, never an admin's: D68), the audit log. `GET /api/admin/businesses/{id}`: one business at a glance (D69). `GET /api/admin/overview?days=7`: what every agent did (D72). `GET /api/admin/failed-turns`: turns that ended with the polite line (D73) |
| GET | `/api/stats?days=7` | The owner's dashboard (D144): today, yesterday and the last `days` days (1 to 90) in the business's own days, each with chats the agent answered, how many it handed off, sales (orders, not cancelled or unpaid) and their total, chats that bought, where they came from (chat, page, team) and leads not sold; and the currency |
| GET | `/api/desks` | The kinds of business and each one's config JSON Schema |
| GET/POST/PATCH | `/api/agent` | The business's one agent: create as a kind, read, edit; problems per field |
| POST | `/api/agent/logo` | The business's logo (multipart): saved as a 256 px JPEG (transparent is white) and set as `widget.logo` → `{url}` (D82) |
| POST/GET/DELETE | `/api/agent/documents` | Add pasted text (JSON) or a file (`/documents/file`, multipart), list, read one, delete (D34) |
| POST | `/api/products/import` | The same list from a CSV file's text; problems per row, nothing saved until it's clean; `dry_run` only checks (D50); `mapping` says which of the file's columns is which of our fields (D53) |
| POST | `/api/products/import/suggest` | The file's columns, a few sample rows, our fields, and a suggested match by the column names for the owner to check (D53, D115) |
| GET/PUT | `/api/products` | A shop's products, with their stock and `variants`, replaced whole. A variant's size or colour that isn't the product's is a 422, and so is a photo new to the list that isn't one of ours (one saved before is kept, D122). Its CSV import adds and updates instead (D120) |
| GET | `/p/{public_key}/{code}` | A product's public page, HTML, no login (D121) |
| POST | `/p/{public_key}/{code}?c=` | Its *Buy now* form: the check page, then with `heard` *Place order* saves it and goes to `/o/<id>`; the receipt goes to chat `c` (D130) |
| GET | `/o/{order_id}` | An order's page for the buyer, HTML: status, steps, items, total; not their name, mobile or address (D130) |
| POST | `/api/products/photos` | A product photo (multipart): saved as a JPEG and embedded → `{url}`; the product keeps it once its `photos` are saved with that URL (D57) |
| GET | `/media/…` | When the photos are on this server's disk, not in R2 (D101). A photo: a product's (`<workspace_id>/products/<id>.jpg`) one a visitor sent (`<workspace_id>/chat/<id>.jpg`) (D57), or the business's logo (`<workspace_id>/logo/<id>.jpg`, D82) |
| GET/PATCH | `/api/shop/orders…` | A shop's orders; one; move its status (D45, D96; the buyer hears confirmed, shipped or cancelled in their chat, D105); once it ended, `paid_in` and `reason` (D131) |
| GET/POST/DELETE | `/api/shop/coupons…` | A shop's coupon codes with their uses; add one (`{code, kind, value, ends_at, max_uses}`, a 409 for a code it has); delete one by code (orders keep their discount) (D133) |
| GET/PUT | `/api/shop/posts…` | The Page's posts the agent knows, with their products; `PUT …/{post_id} {products}` tags one (codes, at most 10; a 404 for one not in the list) (D142) |
| POST | `/api/shop/offers` | `{text, kind, value, ends_at, within_hours}`: sent to every chat that wrote in the last `within_hours` (24 by default, up to 168), inside the window, and saved as a coupon only they may use until it ends; `{code, sent_to}` (D140) |
| GET | `/api/shop/buyers?q=` | A shop's buyers by mobile, the latest to order first: orders, delivered, returned, cancelled, spent, name, channels, conversations (D132). Every shop order also has `buyer`, its mobile's counts |
| POST | `/api/shop/orders` | An order the team types for a buyer who called: `{lines, name, phone, address, district, note}`, priced and checked as the chat's, saved `confirmed` with no yes, the owner alerted (D131) |
| GET | `/api/shop/orders.csv` | The list as a spreadsheet's file, with the list's `status` and `q`: UTF-8 with a BOM, money in taka, the business's own time, a mobile as `01812-345678`, a cell that starts like a formula given a `'` (G6, D131) |
| GET/PATCH | `/api/messages…` | Messages for the team, from any desk; one; move its status |
| GET/PATCH | `/api/tickets…` | Complaints the agent flagged (`?status=open`), each with who has its chat; `PATCH {status}` solves one or opens it again (D138) |
| GET/PATCH | `/api/conversations…` | Conversations (`?status=` `active`, `needs_human` or `human`), and one with what was said: each line's `photo` (the visitor's) and `photos` (shown by the agent), D57; `PATCH {status: "active"}` marks a hand-off handled (D83). Each has `assigned_to` and `assigned_email`; `?assigned=me` or `none` filters by who has it; `PUT …/{id}/assignee {user_id \| null}` gives it to someone (D126); `GET …/counts` is `{needs_human, mine, unassigned}`, the chats waiting for a person (D129); one also has its `visitor` (`id`, `blocked` and why, by whom, until when, `ai_off`), for Block and AI off (D127); `?limit=` gives only the newest lines and `?before=<index>` the ones before a line, with `more`, `count` (every line), each line's `index` and `history_from` (D157). Done and snoozed chats are left out (`?done=true`: only the done ones); `?q=` searches every chat's name and what was said; `PUT …/{id}/done {done}` and `PUT …/{id}/snooze {until \| null}` (D128); `PUT …/{id}/takeover {on}` takes the chat over or gives it back (D135); `PUT …/{id}/labels {labels}`, `?label=` filters; `GET/POST …/{id}/notes {text}` the team's notes; `POST …/{id}/reply {text}` answers from the inbox and takes the chat over (D136); `POST …/{id}/assist {kind, question}` is the model's summary, answer or draft for the team, `{text}` (D137); `GET …/live` is a stream of `changed {conversation_id}` frames for the inbox, one per chat that changed in this business, and a `ping` every 20 seconds (D158) |
| GET/PATCH | `/api/leads…` | Call backs the agent took (`?status=open`); `PATCH {status, why}` is the team's tap: `sold`, `not_now`, or `lost` with why; `GET /api/leads/why` counts the whys, most first: why we lose sales (D139) |
| PATCH | `/api/visitors/{id}` | `{blocked}` or `{ai_off}`: the team blocks or unblocks a visitor, or turns the AI off or on for them (D127) |
| GET | `/api/visitors/blocked` | Who is blocked now, with why, by whom, until when and their latest chat (D127) |
| GET/PUT/DELETE | `/api/agent/telegram` | The business's Telegram bot: its username, `t.me` link and the owner's alerts link; connect it with its token; disconnect it (D42, D43) |
| GET/PUT/DELETE, POST | `/api/agent/sheet`, `/api/agent/sheet/suggest`, `/api/agent/sheet/sync` | The list's linked Google Sheet: its state; link it with a checked mapping; unlink it; suggest a mapping; sync now (D58) |
| GET/PUT/DELETE, POST | `/api/agent/courier`, `/api/agent/courier/sync` | The shop's linked Steadfast or Pathao: its kind, store and problem; link it after checking the keys; unlink it; book and follow now (D90, D113) |
| GET/PUT/DELETE, POST | `/api/agent/woo`, `/api/agent/woo/sync` | The shop's linked WooCommerce store: its state; link it with a Read/Write key; unlink it; sync now (D75) |
| POST | `/api/woo/hook/{workspace_id}` | A linked store's webhook: checked by `X-WC-Webhook-Signature` with the link's secret, a sync started 2 seconds later (D78) |
| POST | `/api/telegram/{channel_id}` | Telegram's webhook: checked by its secret header, answered after the response |
| GET/PUT/DELETE | `/api/agent/messenger` | The business's Facebook Page: its id and `m.me` link; connect it with its id and a Page access token; disconnect it (D60, D61) |
| GET | `/api/messenger` | Facebook's webhook check: echoes `hub.challenge` when `hub.verify_token` is `MESSENGER_VERIFY_TOKEN` |
| POST | `/api/messenger` | Messenger's webhook, one for every Page: checked by `X-Hub-Signature-256` with `META_APP_SECRET`, each message routed by its Page id, answered after the response |
| GET/PUT/DELETE | `/api/agent/whatsapp` | The business's WhatsApp number: its id; connect it with its business account's id, the number's id and a system user's token; disconnect it (D103) |
| GET, POST | `/api/agent/whatsapp/login`, `/api/agent/whatsapp/signup` | Connect with WhatsApp: our app's id and the Embedded Signup configuration; connect the number from the popup's code and ids (D155) |
| GET/POST | `/api/whatsapp` | WhatsApp's webhook, one for every number: checked as Messenger's (the same verify token and app secret), each message routed by its `phone_number_id`, answered after the response (D103) |
| GET | `/pay/{id}` | The visitor's payment link: off to the gateway to pay for an unpaid order (D47, D48); with both connected, a button for each, then `?via=sslcommerz` or `?via=bkash` (D109) |
| GET | `/pay/{id}/bkash` | bKash sends the visitor back here with `paymentID` and `status`; only a `success`, executed with the business's bKash account, counts (D109) |
| POST | `/pay/{id}/back` | SSLCommerz sends the visitor back here (paid, failed or cancelled); a page says which |
| POST | `/api/payments/sslcommerz/{id}` | SSLCommerz's IPN: the same check, for when the buyer closed the tab |
| GET | `/api/billing` | The business's plan, `paid_until`, `active`, replies used in the last 30 days and the plan's limit, and the plans it can buy (D106). Owners only |
| POST | `/api/billing/checkout` | `{plan, months: 1 or 12, gateway: sslcommerz or bkash}` → `{url}`: our gateway's page; a 400 when that gateway isn't set up (D106, D109). `/api/billing`'s `gateways` lists those that are. Owners only |
| POST | `/billing/{id}/back`, `/api/billing/sslcommerz/{id}` | The owner's return and the gateway's notice; only `val_id`, checked with our store, counts (D106) |
| GET | `/billing/{id}/bkash` | bKash's return for a plan: only a `success` `paymentID`, executed with our bKash account, counts (D109) |
| PUT | `/api/admin/businesses/{id}/plan` | `{plan, paid_until}`: set by hand, for a payment made another way; audited (D106) |
| GET/PUT/DELETE | `/api/agent/sslcommerz` | The business's own SSLCommerz store: its id and test mode; connect it (checked with SSLCommerz first); disconnect it (D49). The password never comes back |
| GET/PUT/DELETE | `/api/agent/bkash` | The business's own bKash account: `{app_key, app_secret, username, password, sandbox}`; connect it (checked by granting a token); disconnect it (D109). Only the username and test mode come back |
| GET | `/health` | DB reachable |

Lists that grow (shop orders, messages, conversations, the
audit log) come a page at a time, newest first (D46): `?limit=` (1 to 100, default
100) and `?before=` the last row's time (`created_at`; `updated_at` for
conversations; `at` for the audit log). Fewer than `limit` rows means it was the
last page.

Everything under `/api` except chat, desks, auth and the Telegram, Messenger, WhatsApp and Woo webhooks needs
`Authorization: Bearer <token>`.

**Three roles** (D23, D97). An **owner** belongs to one business: the token says which
(rule 6), and they only ever see it. A **moderator** is someone the owner added to it:
they run the inbox (conversations, messages, orders, calls, the
lists) and can read the settings, knowledge and channels, but a change under `/api/agent`,
`/api/billing` and anything under `/api/team` are a 403 for them (`deps.owners_only`), the
saved replies included (D151, D153). The Telegram
bot's `owner_link` comes back empty for them (it makes you the one who gets the owner's
alerts); their own link is on `/api/auth/me` (D148). The owner adds one by
email; the user is made with a password no one knows and the email has a reset code, so
they join with `POST /api/auth/reset` (or, once it's expired, Forgot password), which
verifies them. One account per email, so an email that has one can't be added. Removing a
moderator deletes the user, and the database is asked on every one of their requests, so
they're out at once. Every write an owner or moderator makes, once the route succeeds, is a
`team_events` row (the route and the ids in its path; the dependency writes it after the
route returns), which the owner reads at `/api/team/activity`.

**Who has a chat (D126).** A conversation can have one team member (`assigned_to`). The owner
(or an admin inside the business) gives any chat to anyone on the team, or to no one. A
moderator can only *Take* an unassigned chat for themself and *Give back* their own; giving one
to someone else, or taking a colleague's, is a 403, so the owner is the one who moves chats
between people. With `handoff.auto_assign` on (off at first), a hand-off with no one on it goes
to the joined moderator with the fewest open chats (`needs_human` or `human`), the longest on
the team first when it's even; with no joined moderator it stays unassigned. Having a chat
doesn't stop the agent: taking over is still a reply from the team (D43). Each move is a
`team_events` row like any write. So the team can see who is busy, each member on
`/api/team/members` has `open_chats`, the same count a hand-off goes by; and
`GET /api/conversations/counts` gives the inbox's tabs their numbers: how many shown chats wait
for a person (`needs_human`), and of those the caller's (`mine`) and no one's (`unassigned`)
(D129).

**Alerts for whoever has the chat (D148).** A moderator links their own Telegram with the
business's bot from the link on `/api/auth/me` (`telegram_link`: `/start member_<user id><code>`,
the code from the bot's secret and their id, so connecting a new bot changes it and unlinks
everyone). From then on an alert about a chat they have goes to them, not the owner; a chat no
one has, or whose member hasn't linked, alerts the owner as before. The owner giving someone a
chat tells them ("👉 This chat is yours now", with its last lines). A moderator answers an alert
the same way the owner does: a reply, ".", or `/done`; a reply is a `team_events` row
(`Telegram reply`) under their name.

**Who's available (D149).** Each member is on or off for new hand-offs (`users.available`).
Auto-assign picks only an available moderator; with none, the chat stays unassigned and the
owner is alerted. A moderator switches themself at `PUT /api/auth/me/available`, the owner
anyone at `PUT /api/team/members/{id}/available`. A chat someone already has stays theirs.

**The team's numbers (D150).** `GET /api/team/stats?days=7` (the owner's) lists each member:
open chats now, and in those days their replies to visitors (from the inbox or Telegram), the
chats they replied in, the tickets they marked solved and the leads they marked sold, counted
from `team_events`. A ticket or lead counts for whoever tapped it, and only while it stays so.

**Finding a chat (D128).** The list leaves out a chat that is *Done* (`done_at`) or snoozed
(`snoozed_until` still to come). *Done* lasts until the visitor writes again: saving their next
turn clears `done_at` and `snoozed_until`. *Snooze* takes a time in the next 30 days
(`MAX_SNOOZE_DAYS`); `ConversationService.run` checks every `SNOOZE_CHECK_SECONDS` (60), clears
the ones that are due and alerts the owner ("⏰ A chat you snoozed is back", with its `Ref:`).
The same round nudges an unfinished order (D139): a shop chat, `active`, last touched 1 to 3
hours ago, whose last turn (not an `assist` one) ran `place_order` with no order saved since,
gets "Shall I confirm your order? Tap Yes, or tell me what to change." with *Yes* and *Change*,
through `tell_in_window`. `nudged` is set when tried, so never twice in a chat. A *Yes* is
answered by the agent: the read-back is still the state's, so `place_order` saves it.
`?q=` matches the name, or the messages as text (`CAST(messages AS TEXT) ILIKE`), so a phone,
an order code or any words find it, in done and snoozed chats too; `%` and `_` are taken as
letters. JSON is stored with real letters (`json_serializer`, `ensure_ascii=False`), so Bangla
is found too; rows saved before keep their `\u` escapes. Postgres full text waits until
`ILIKE` is too slow.

An **admin** runs the platform and belongs to
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
turns (D71) for today and the last N days: turns, conversations started, tokens (and how many were cached, D154), cost (priced
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
`handoff {message, phone}`, `photos {items: [{name, price, url}]}` (D57), `card {kind, data}`
(D110), `choices {items: [text]}` (just before `done`, when the reply has buttons, D99, D110),
`done {interrupted}`, `error {message}`.

Every turn logs one JSON line, `turn finished`, with its trace: steps, timings,
tokens, cost, error (rule 7). The same save adds a row to `turns` (D71, changing D15's
"no trace table"): the sums without the steps or anyone's words, for the admin.

### Limits on a public chat (D51)

The chat needs no login, so `VisitorService` (`services/visitors.py`) stops timepass from
spending a business's turns. Every turn names its **visitor**: the web page keeps a random token
in the browser (`visitor_id` in the chat body, `?visitor=` on a call's offer; a 422 without it),
and Telegram's is the chat id.

```
start_turn ─► visitors.admit: blocked? the plan ended, or its replies used up (D106)?
                              the visitor's turns today ≥ VISITOR_DAILY_TURNS (30)?
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

**Block and AI off (D127).** The team (owner or moderator) decides about one visitor from their
chat: `PATCH /api/visitors/{id} {blocked | ai_off}`; `GET /api/visitors/blocked` lists who is
blocked now, with their latest chat. A visitor is who `key_of(channel, contact)` names, so a
block covers all their chats on that app (a web visitor's chats and calls share one).

```
any message ─► visitors.let_in (first, before the photo, the conversation or a count):
                 blocked? (a why, and no end or an end still to come)
                   first time since the block ─► 429 VisitorLimited with the hand-off line
                   after that ─► VisitorSilenced: a messaging app sends nothing; the web chat
                                 still gets the 429, as the page shows the error itself
                 ai_off ─► the conversation becomes `human`, the message is saved and forwarded
                           to the owner (D43's path): no turn, nothing paid
                 AI on again ─► their next message gives the chat back (`_given_back`) and
                           is answered
a messaging app's message ─► visitors.arrived: FLOOD_MESSAGES (20) in a minute ─► blocked
                             `flooding` for BLOCK_HOURS (counted per message, before D117's
                             batching; in memory, like D52)
```

- **A block keeps nothing.** A blocked visitor's messages aren't saved or shown: they're spam,
  and keeping them would mean a turn row for nothing. AI off keeps everything, since the team
  answers it.
- **Three reasons.** `team` (Block; lasts until Unblock), `flooding` and `off_topic` (D51's
  strikes; both `BLOCK_HOURS`). Unblock clears the why, the strikes and the notice, so a later
  block tells them once again.
- The web chat's own burst limit (6 a minute, D52) answers before a flood could build there.

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

### Importing a list: their columns, our fields (D50, D53, D115)

A business's spreadsheet has its own column names ("Item Title", "MRP", "Qty"). It doesn't have
to rename them to ours: the owner matches each of our fields to one of their columns, with
Python's suggestion from the column names to start from (no model, D115). Three steps, each knowing nothing of the others:

```
their file ─► 1. read: rows as {their column: text}            csv_import.peek / parse
           ─► 2. match: {our field: their column}               Python suggests, the owner decides
           ─► 3. a row becomes our item: Product, MenuItem…     the desk's own row(), as before
```

**A desk lists its fields once:** `PRODUCT_COLUMNS` in its
`values.py`, each a `Column(name, about, required, aliases)`. `about` is what the field holds, in
plain words, for the owner's screen; `aliases` are the other names sheets give it, in English,
Bangla and Banglish (`"mrp"`, `"dam"`, `"দাম"`). The required ones are what `parse` needs.

**The flow:**

1. `POST /api/products/import/suggest {csv}` → the file's `headers`, its first 3 rows as
   `samples`, our `columns`, a `mapping` (`{"name": "Item Title", "price": "MRP"}`, a field with
   no column left out).
2. The owner's screen shows each of our fields with a dropdown of their columns, the suggested
   one picked. They change what's wrong.
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

**The suggestion is the column names, in Python** (D115). `csv_import.suggest` reads the headers
and 3 sample rows; `match` scores each header against each field's name and `aliases`: 1 for the
same words ("Price (৳)" and `price`), 0.9 when they're inside it ("Selling Price"), and a close
misspelling ("Catagory", "Colors") by `difflib` at 0.8 alike or more, scored below both. The best
scores are taken first, each field and each column once; a field nothing fits is left for the
owner. A new name owners use is one more alias. Turning "৳1,850" into 185000, "M; L" into a list
or "yes" into `True` stays in the desk's `row()`.

**Later sources** (Shopify, a POS) are another step 1: something that returns rows as
`{their field: text}`. Their field names are the same for every store, so their mapping is
written once in code and the owner never sees step 2. Nothing in step 3 changes. The first
one is a Google Sheet that stays linked (D58, below); the second, a WooCommerce store (D75).

### A linked Google Sheet (D58)

Most small shops and chambers keep their list on paper or in Excel, not in software with an
API. A Google Sheet is the next step up for them, and it's an import that repeats: the same
three steps as above, every few minutes, with the owner's match saved. The list it fills is
the desk's own (`products`), so the agent, its tools and the prompt
don't change: they read our table as before.

```
owner: pastes the sheet's link ─► suggest (as D53) ─► checks the match ─► PUT /api/agent/sheet
every SHEET_SYNC_MINUTES (5):
  fetch the sheet as CSV ─► same text as last time? stop
                         ─► parse with the saved mapping (the desk's row(), as an import)
                         ─► a row has a problem? keep the old list, note it, alert the owner once
                         ─► merge (the shop's stock, below) ─► replace the list
                            (a shop's: add to it, D120)
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
- **Everything comes from the sheet, except the shop's stock.** Names, prices, sizes,
  `available`: the sheet's, every time, since nothing else changes them. Stock
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
  less what's held. The owner's
  screen says it in one line: *"Write what's on your shelf. We take off the orders you haven't
  shipped yet."*
- **A problem keeps the old list.** A row the desk's `row()` can't read (as an import: nothing
  saved until the whole sheet is clean), or a sheet that can't be fetched: the list stays as
  it was, `problem` says why (the first 3 rows' problems, or "couldn't open the sheet"), and the
  owner gets one Telegram alert when the problem first appears, not every 5 minutes (a new
  `TeamService.notify(ws, text)`: an alert with no conversation, so no `Ref:` line).
  A sheet that can't be fetched is read whole again once it's back.
  The next clean sync clears it.
- **A linked sheet adds and updates only its own products** (D120, below), so `PUT
  /api/products` and its import still work while it's linked.
- **One sheet per agent**, because an agent is one desk with one list. The desk names its
  fields (`Desk.columns`, the same as the import's) and its store takes the sheet's text
  (`Store.take_sheet(text, mapping, seen) -> seen`): the
  shop merges, in one transaction with reading the orders that hold stock (`merge`,
  `take_off`, `HOLDS` in `values.py`).

**Routes:**

| Method | Path | What |
|---|---|---|
| POST | `/api/agent/sheet/suggest` | `{url}` → fetches it, then the same as D53's suggest: its columns, 3 rows, our fields, the suggested `mapping` |
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
a chat turn saves or changes an order, or its payment comes in ─► push now, in the background (D77, D105)
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
  time, so the sync and a turn never write the same order twice. An order paid online first
  is pushed the same way once `PaymentService.settle` places it (D105). A push that fails says
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
- **While linked, the store's products are kept in step** (D120, below): `PUT /api/products`
  and the import still work; linking a sheet too is a 409 `sheet_linked`. The owner moves orders in HeyDesk as before; each move
  reaches Woo with the next push. A change made to our order in Woo's admin isn't read back.

**Routes:**

| Method | Path | What |
|---|---|---|
| PUT | `/api/agent/woo` | `{url, key, secret}` → checks the key (above), saves the link and takes the list; answers like GET |
| GET | `/api/agent/woo` | The store's address, `synced_at`, `problem`, how many products. Never the key or secret |
| DELETE | `/api/agent/woo` | Stops syncing. The list stays and can be edited again; orders already in Woo stay there |
| POST | `/api/agent/woo/sync` | Syncs now (push, then pull); returns what GET does |
| POST | `/api/woo/hook/{workspace_id}` | Woo's webhooks: a signed post starts a sync 2 seconds later; no login (D78) |

### A shop's list from many places (D120)

A shop's products come from a CSV file, a linked Google Sheet or WooCommerce store, and edits
by hand. **None of them replaces the list; each adds to it and updates what it has.**

```python
def added(ours, found, gone=None) -> list[Product]:     # values.py
    kept = [p for p in ours if p.code not in gone]
    for p in found:
        same = the one free p with p.code, else the only free one with the same name
        same ? kept[its place] = p (keeping its photos if p has none) : kept.append(p)
    return kept
```

- **The same product** is the one with the same code, or else the only one with the same name
  (spaces and capitals aside). Sizes and colours are inside a product (D56), so the name is
  enough. Two of ours with that name: it's added, not guessed. A matched product takes the
  source's code, so its next sync finds it by code.
- **A file** (`POST /api/products/import`, `ShopStore.add_products`) never deletes; it answers
  `added`, how many were new.
- **A linked sheet or store** removes only the products **it** brought last time and has no
  more: the codes in the sheet's `seen`, or in the Woo link's `ids`, less the ones read now.
  The rest of the list stays. The first link deletes nothing.
- **The source wins for its own products:** a hand edit to one stays until its row changes
  there (the sheet's text, or Woo's products, changed). One sheet *or* one store, still.
- Given up: a product the owner deleted here comes back when the sheet or store next changes;
  delete it there. Two sources with different codes for one product swap its code at each sync.

### A product's page (D121)

Tapping a product in a chat opens its own page: `/p/<the agent's public_key>/<code>`, with
`?from=messenger` or `?from=telegram` only where the page shows a button back to that app, and
`c=<chat>` when a chat sent it (D160). The owner's products list has *Open its page* and *Copy
its link* on each product, for the team to paste the link in a reply.
`public_key` is already public (it's in the chat link); the code is the product's. The page
(`desks/shop/page.py`) is server-rendered HTML: the photos, name, price (and the price before a
sale), the sizes and colours in stock (`Product.in_stock`), the description, and "To order, go
back to the chat and say *Order [code]*". No JSON and no `/api` link; a policy header lets
nothing but its photos load and no script run. An unknown key, a key that isn't a shop's, or
an unknown code is the same 404 page.

- **The look** (D159): a storefront, still with no script. Two columns on a wide screen (the
  photos stay in view, the details scroll), one on a phone; the shop's initial and name in a bar
  on top. Up to 3 photos with a thumbnail each: a radio button per photo, and CSS shows the
  checked one. The price, the price before a sale struck through with *Save N%*, and a stock
  line: *In stock*, *Only N left* (the product's own count at 5 or under; not with variants),
  or *Sold out*. The sizes and colours in stock, the description (its line breaks kept), the
  videos, *Buy now*, the chat's *Order [code]*, then each delivery area's charge and days and
  *Cash on delivery*. In the form, the size and colour are chips to tap (radio buttons) in
  place of lists. `og:` tags (name, description, the first photo's whole link from
  `ChannelService.whole`) make a link pasted in an app show the product.

- **Back to the chat:** on Messenger and Telegram a button opens `m.me/<page id>` or
  `t.me/<bot>` (`ChannelService.chat_link`, from `from`). The website chat opens the page in a
  new tab, and WhatsApp in its own browser, so closing it goes back; no button. (WhatsApp's
  link needs the number, which we don't keep: the number's id isn't it.)
- **The links** (`ChannelService.product_page`): web chat, the card itself and a *Details*
  button beside *Order this* (D123); Messenger, the
  card's tap (`default_action`) and a *Details* button beside *Order this* (D99); Telegram, a
  *Details* URL button beside *Order this*; WhatsApp, a `Details: <link>` line in the caption.
- **Order this** on the website chat sends "Order [code]", as Messenger's postback does (D123),
  so `# Right now` tells the web chat too that a tapped button arrives as its words (`TAPS`).
- **Buy now** (D130): a `<details>` form on the page, no script: size and colour (what's in
  stock), how many, name, mobile, address, district (a list of the 64) and a note, with each
  area's charge and days. It posts back to the page. `form_order` (`desks/shop/tools.py`)
  checks it as `place_order` does (`_priced`, `_short`, `match_zone`, `mobile`), and
  `new_order` builds the order and its read-back for both. The first post shows **Check your
  order**, the order read back, with the details and `heard` (that read-back) in hidden
  fields; *Place order* posts them again, and `confirm(heard, order, read_back)` saves it only
  if Python's read-back is still the same (a price or a charge changed: the check page again).
  *Change* goes back to the filled form. The tap is the yes, as a tapped *Yes* is in a chat.
- **Back to the chat:** each card's link carries `c=<conversation id>` (`Reply.conversation_id`,
  the web chat's own). Only one of this agent's conversations counts. The order is linked to
  it, the owner's alert is the chat's (with `Ref:`), and the receipt goes into it as the team's
  message (`tell_visitor`): the web chat shows it, the apps send it, the agent sees it. With no
  chat, the order still saves and the owner hears of it with `notify`.
- **`/o/<id>`**: where the order is, from its long random id (as `/pay/<id>`, D47): status,
  steps, the courier's last words, items, total and what the rider collects, and *Pay* when it
  waits for the delivery charge. Not the name, mobile or address: a link can be passed on.
  Every receipt links it (`order_url`): the web chat's *Follow your order*, a line in the apps.
- Given up: two quick taps on *Place order* save two orders, and the form isn't under the chat's
  daily limits (D52). Neither is worth code before a shop sees it happen.

### Remembering a visitor (D114, D123, D124)

A visitor is the same chat on a messaging app, or the same browser on the web chat and its calls.
Two things make them feel known:

- **Their last order or message** (`Store.before`): in an earlier conversation *or this
  one*, so a second order in the same Messenger chat is offered the first one's name, phone and
  address instead of asking again (D123). The desk's `returning()` section says to offer them
  once, when the details are needed.
- **What they told the agent** (D124): the shared `remember` tool keeps a short note when the
  visitor mentions something worth keeping ("likes dark colours", "buying for her son, 8",
  "allergic to penicillin"). Notes live on the `visitors` row the limits already keep
  (`web:<token>`, `messenger:<psid>`…), as `{about: note}`: a note on the same `about` replaces
  the old one, and only the newest 20 stay. Every turn, `# What you remember about them` lists
  them after the desk's sections; the rule says to use them naturally, not to read them out or
  ask about them. They're the visitor's words, so they're never formatted (`fill`) and never
  logged. The owner can't see or clear them yet.

### Telegram (D42)

A business connects **its own bot**: it makes one with @BotFather and sends the token to
`PUT /api/agent/telegram`. `ChannelService` checks the token (`getMe`), then sets the bot's
webhook to `PUBLIC_URL/api/telegram/{channel id}` with a random secret, and saves the row.
`GET /api/agent/telegram` gives the `t.me` link the business shares with its customers.

```
Telegram ─► POST /api/telegram/{id}  (secret header checked, else 404) ─► 200 at once
         then: /start      ─► the chat starts afresh (D157), and the greeting is sent
               text        ─► "typing…" ─► ConversationService.reply(agent, "telegram", chat id, text)
                               ─► the whole reply as one message, then the hand-off line if any
               a photo (the shop) ─► the same, with the photo and its caption (D63)
               anything else (a voice note, a sticker) ─► "I can only read text messages for now."
```

- **Which conversation:** `conversations.contact` is the chat id, and a contact keeps one
  conversation (D157). When it's full (`MAX_TURNS` since the agent's memory starts) or quiet
  for `CONVERSATION_IDLE_HOURS` (12), the agent starts afresh in it: `history_from` moves to the
  end of `messages`, the cart (`state`) is emptied and `nudged` is cleared, so yesterday's
  half-order doesn't return. The model's history is `messages[history_from:]`; the team still
  sees the whole chat, with its labels, a line where the agent's memory starts, and older
  lines loading as they scroll up (`GET …/{id}?limit=&before=`).
- **Another messaging app** is the same shape: its port and adapter, a webhook route, and
  `ConversationService.reply` for the turn. WhatsApp was (D103).
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

**Comments on the Page's posts (D141).** The app is subscribed to the Page's `feed` too. A
`feed` change with `item: comment` and `verb: add` runs `ChannelService.answer_comment`:

1. The Page's own comments (`from.id` is the Page, our public replies among them) are skipped.
2. A `post_comments` row per post and person (unique), so a second comment from them gets
   nothing: the chat is open by then.
3. What the post is about is found **once**, at its first comment, and kept as a
   `page_posts` row (D142): its words (`MessengerApi.get_post`) and, on a shop, the codes of
   the products they name (`post_products`: longest name first, each found name taken out, in
   the post's order), or, naming none, the one product whose photo is most like the post's
   picture (D57). Every later comment, from anyone, uses the row: no second fetch or photo
   search. The owner can set a post's products (`PUT /api/shop/posts/{id}`, `tagged`). The
   products' price and stock are read from the list each time (rule 1).
4. The replies, from Python: one product, the private reply says its name, price and sizes,
   or that it's sold out; several, it is their cards side by side (R2's carousel, each with
   *Order this*), falling back to their names and prices if Meta refuses the carousel; none, a
   plain thanks. The public reply is the price or the range, "৳890–৳950. Sent you the details
   in Messenger 📩" (no price when there's no product or it's sold out).
5. The private reply (`me/messages` with `recipient.comment_id`) opens a Messenger chat; Meta
   answers with the buyer's PSID, which is the chat's contact (G43). The comment, as
   `(a comment on our post: "…") dam koto?`, and the private reply go into that chat
   (`ConversationService.commented`), so their reply in Messenger carries on from it.

6. **One more reply under the comment (D142):** `ChannelService.run` checks every
   `SNOOZE_CHECK_SECONDS`; a comment answered 2 hours ago (and less than a day) whose chat has
   had no turn since gets "We sent you a message in Messenger: check your inbox 📩", once
   (`post_comments.nudged`, set when tried).

It needs `pages_manage_engagement` and `pages_read_user_content` (App Review) on top of D119's.

### WhatsApp (D103)

WhatsApp's Cloud API is Messenger's shape: our one Meta app, **one webhook for every number**
(`PUBLIC_URL/api/whatsapp`, field `messages`, the same `MESSENGER_VERIFY_TOKEN` and
`META_APP_SECRET`), each post signed and routed by its `metadata.phone_number_id`.

A business connects **its number** with three things from its Meta Business settings:
`PUT /api/agent/whatsapp {token, account_id, phone_id}`, a system user's token, the WhatsApp
Business Account's id and the number's id. `ChannelService` refuses a number another business
has; `WhatsAppApi.subscribe` reads the number with the token (the check) and subscribes our app
to the account's messages. The number's id is the `address`.

```
Meta ─► POST /api/whatsapp (signature checked, else 404) ─► 200 at once
       then, for each message (statuses are skipped): its number's id ─► the channel
       ─► answer_whatsapp: puts it in _answer's shape
          (text's body; a tapped button's id; an image's media id and caption)
       ─► the same turn as Messenger's; the sender's number (`from`) is the contact
```

- **Buttons (D99):** up to 3 choices are reply buttons, more a list ("Choose", up to 10); a
  tap's `id` is the choice, so it arrives as its words. A reply over 1,024 characters (the
  limit with buttons) goes as plain text and the choices are typed.
- **Photos (D57):** a buyer's `image` is downloaded by its media id (two calls, both with the
  token) and its caption is the words, as on Telegram. `show_photos` sends one message a
  product: the photo as the header, "Cotton Kurti · ৳950", and an Order this button whose id is
  "Order [kurti]". Meta fetches the photo, so it needs a public URL, as Messenger does.
- **Alerts stay on Telegram** (D43); the owner's reply goes out from the number. As on
  Messenger, only within 24 hours of the visitor's last message: a later one fails and is
  logged (outside it WhatsApp needs a paid, approved template).
- **Disconnecting** deletes the row only: our app stays subscribed to the account, which may
  have other numbers, and this number's messages find no channel.
- The token goes in the `Authorization` header, never a URL.

### Feeling human on the chat apps (D117)

A buyer writes like a person: "hi", then "dam koto", then a photo, in three bubbles, and
sometimes replies to an older message. The agent answers like a person.

```
a message ─► (Messenger: "Seen" at once) ─► _gather: waits CHAT_WAIT_SECONDS (3)
          ─► another came meanwhile? this one stops; the last one answers them all
          ─► _turn: each message's words a line, a photo downloaded, a quoted message
             put before the words that reply to it ─► one ConversationService.reply
             (typing… while it runs) ─► one reply
```

- **"typing…" only when the agent answers** (`ConversationService.answers`): not for a visitor
  who is blocked or has the AI off (I2) or the business turned it off for all (D165), or a chat the team
  has taken over (D135), since no reply is coming from us. WhatsApp's "typing…" also marks
  their last message read, and lasts until the reply, so it's sent once.

- **The wait is in memory** (`ChannelService.waiting`, chat → messages not answered yet), so it
  needs one server process. `/start`, the owner's chat and `/start owner_` don't wait. Tests set
  `CHAT_WAIT_SECONDS=0`, so each message is its own turn unless a test waits.
- **Several photos:** the last one is the turn's photo (one photo a turn, D57).
- **A reply to an earlier message** reads `(replying to: "Beef Bhuna") is it spicy?` (the quote
  cut at 300 characters). A quoted photo is the turn's photo when the reply has none.
  Telegram sends the quoted message whole (`reply_to_message`); Messenger only its id
  (`reply_to.mid`), so `MessengerApi.get_message` fetches it, the buyer's or the Page's own;
  WhatsApp only its id (`context.id`), and Meta has no way to fetch it, so the server keeps the
  buyers' last 10,000 messages by id (`whatsapp_said`). A quote from before a restart isn't
  known: the reply is read on its own.
- **A reply to a product photo (D156)** names the product: "I want this" is read as
  `(replying to: "Cotton Shirt · ৳950") I want this`. Telegram's photo carries its caption.
  On Messenger and WhatsApp the server keeps the product cards the agent sent, by message id
  (`cards_sent`, the last 10,000; `send_cards` returns the ids): WhatsApp's are one message a
  product, so the reply names one; Messenger's carousel is one message, so it names them all
  ("Cotton Shirt · ৳950; Polo · ৳800") and the agent asks which. Looked up before asking
  Facebook, so a reply to a card costs no call. In memory, like `whatsapp_said`.
- **Sir or Ma'am:** a platform rule (docs/prompt-rules.md) has the agent address the visitor as
  the business does, without guessing which, and keep it once known.

### Connect with Facebook (D118)

Copying a Page's id and access token out of Meta's dashboard (D61) is too hard for a shop
owner. The owner's page has a **Connect with Facebook** button instead; the id-and-token form
stays behind a small link.

```
owner's page ─► GET /api/agent/messenger/login ─► {app_id: META_APP_ID, config_id}
            ─► Facebook's JS SDK: FB.login({config_id}) ─► Facebook's window: log in, allow the Pages
            ─► a short user token, in the browser
            ─► POST /api/agent/messenger/pages {user_token}
                 MessengerApi.pages: oauth/access_token (fb_exchange_token, with the app's id
                 and secret) ─► a long-lived user token ─► me/accounts ─► each Page's token
                 (from a long-lived login, a Page token doesn't expire)
               ─► [{id, name}]: never a token
            ─► one Page: connected at once; several: the owner picks
            ─► POST /api/agent/messenger/facebook {user_token, page_id}
                 the Pages again, that one's token ─► connect_messenger, as if typed in
```

- **The permissions live in Meta's dashboard (D119).** Our app is a Business app, so its login
  is Facebook Login for Business, which takes a configuration's id (`META_LOGIN_CONFIG_ID`),
  not a list of permissions. The configuration asks for `pages_show_list`,
  `business_management` (a Page in a business portfolio is only listed with it),
  `pages_manage_metadata` (subscribing our app) and `pages_messaging`.
- **Nothing kept from the login.** The user token is sent twice and forgotten; only the chosen
  Page's token is saved, encrypted, as before.
- **Who can use it.** While our Meta app is in development mode, only people with a role on it
  (admin, developer, tester) can log in, which is enough for the demo. For every business, Meta
  wants Business Verification and App Review of these permissions.

### Connect with WhatsApp (D155)

H1's form (a system user's token and two ids) is as hard as the Page's token was, so the
WhatsApp row gets the same kind of button: Meta's **Embedded Signup**, in a popup.

```
owner's page ─► GET /api/agent/whatsapp/login ─► {app_id: META_APP_ID, config_id: META_WA_CONFIG_ID}
            ─► FB.login({config_id, response_type: "code", extras: {version: "v4", setup: {}}})
            ─► Meta's window: log in, pick or make the business, add the number, verify it by SMS
            ─► the browser gets a `code`, and a WA_EMBEDDED_SIGNUP FINISH message with
               {waba_id, phone_number_id}
            ─► POST /api/agent/whatsapp/signup {code, account_id, phone_id}
                 a number another business has: refused first
                 WhatsAppApi.exchange: oauth/access_token (the app's id and secret, the code)
                   ─► the business's token (a system user's, which doesn't expire)
                 WhatsAppApi.register: {phone_id}/register with a random 6-digit PIN
                 ─► connect_whatsapp, as if typed in (it subscribes our app to the account)
```

- **Its own configuration.** Only a configuration of the *WhatsApp Embedded Signup* kind shows
  the number's screens and sends the ids; the Pages one (`META_LOGIN_CONFIG_ID`, D119) stays as
  it is. This one asks for `whatsapp_business_management` and `whatsapp_business_messaging`.
- **The PIN isn't kept.** Registering sets the number's two-step PIN; the owner can reset it in
  WhatsApp Manager if they ever need it.
- **A number on the WhatsApp Business app** can't be added this way (it would leave the app).
  Meta's coexistence (`featureType: whatsapp_business_app_onboarding`) is for later.
- **Nothing kept from the login** but the token, encrypted, as before. The code works once.
- **Who can use it.** In development mode only people with a role on our Meta app. For every
  business we must be a WhatsApp Tech Provider: Business Verification and App Review.

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

### Voice notes (D147)

A voice note on Telegram (`voice`, or `audio`), WhatsApp (`audio`) or Messenger (an `audio`
attachment, `first_audio`) is downloaded like a photo and put in words by the `Transcriber`
port: Groq's Whisper (`whisper-large-v3-turbo`, `adapters/transcriber/groq.py`), which takes the
apps' ogg and mp4 as they are and finds the language itself. `build_transcriber` uses it when
`GROQ_API_KEY` is set, the fake when `LLM_PROVIDER=fake`, and none otherwise. The turn reads
`[Voice note] <words>` as if typed; the reply stays text. A note over `MAX_VOICE_BYTES` (2 MB,
minutes long), one that can't be fetched or heard, or no transcriber, is skipped like a sticker:
alone, it gets `NOT_TEXT`. Nothing is kept but the words in the transcript.

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
- **A Yes button is a typed yes** (rule 2): the model judges it like any reply, and the next
  `place_order` still saves only the details last read back.
- **The limits:** a choice is at most 20 characters (Messenger's quick reply) and 64 bytes
  (Telegram's button data), at most 13 of them; a longer one gets no button and is typed. An
  Order button needs "Order [code]" within 64 bytes.
- **The web chat** shows them too, since D110: a `choices` event at the end of the reply, and a
  tap sends its words as the next message. A call stays words.
- **Postbacks are signed** like messages: they come to the same `/api/messenger`. Our app
  subscribes a Page to `messages,messaging_postbacks`, and the Meta app's webhook needs the
  `messaging_postbacks` field. Telegram's webhook allows `message` and `callback_query`. A Page
  or bot connected before D99 is connected again once to get them.
- The model is told, in the Telegram and Messenger channel lines, that a tap arrives as its
  words, and to reply in the language they've been writing in (the buttons are English).

### Cards (D110)

A tool can show a **card** next to its words: the details laid out, not just said. Python
builds every value (rule 1); a screen only lays it out. `runtime/cards.py` builds them and
documents each shape.

```
a tool's result ─► ToolResult.card {kind, data} ─► the loop yields a Card event (not on voice)
   web chat: a `card` SSE event ─► the dev chat page (static/chat.js) and the Vue chat draw it
   Messenger, Telegram, WhatsApp: Reply.card ─► ChannelService._send_card, each in its own way (D111)
```

| Kind | From | `data` |
|------|------|--------|
| `summary` | `ask_for_yes(read_back, tool, card)`: shop `place_order`, `change_order` | `{title, lines: [{label, qty?, amount?}], rows: [{label, value, at?}], total?}` |
| `receipt` | the same writing tools once they saved: an order | `{title, number, status, rows, total?, pay_url?}` |
| `tracking` | `track_order` | `{number, steps: [{label, done, current}], note}` |

- **Amounts and times:** an amount is `{minor, currency, text}` (`text` from `speech.money`;
  nothing to pay is "free"); a row with a time has `at`, ISO 8601 in UTC, beside its display
  `value`.
- **The read-back stays in the words.** A summary shows the same details as the read-back, but
  the yes is still to the read-back string (D107), and voice and plain-text channels need it.
  The model isn't told about cards: it says what it says without them.
- **Buttons stay `choices`** (D99): Yes and Change under a summary: a tap is only words.
- **A receipt's `pay_url`** is the `/pay/<id>` link a row saved unpaid gets (D47).
- **Tracking:** a desk lists its steps (`STEPS` for the shop); a status off them (unpaid, cancelled, returned) is one
  step on its own. `note` is the status in words.
- **Not kept:** a card is shown when the tool runs; it isn't saved on the message like
  `photos`, so a reloaded chat or the owner's view doesn't show it (§12).
- Cancel tools, `take_message` and `request_exchange` show no card: their read-back is one line.

**On Messenger, Telegram and WhatsApp (D111).** `Reply.card` is the turn's last card;
`ChannelService._send_card` sends it **after the words**, so its buttons are the last thing in
the chat. `card_text` writes it as lines: the title in bold (Telegram's HTML, every value
escaped; WhatsApp's `*stars*`; Messenger has no bold), a summary's lines as "2 × Cotton Kurti —
৳1,800", rows as "Label: value", the total last in bold.

| Kind | Telegram (`send_card`: HTML, a button a row) | Messenger | WhatsApp |
|------|------|------|------|
| `summary` | the text, with Yes and Change | the text, with Yes and Change as quick replies | the text, with Yes and Change as reply buttons |
| `receipt` | the text, with a "💳 Pay now" URL button when `pay_url` | the text; with `pay_url`, a button template with a "Pay now" web_url button (`send_link`) | the text; with `pay_url`, an interactive `cta_url` "Pay now" (`send_link`) |
| `tracking` | "Order O-106", then "✅ Received → 🔵 Confirmed → ⚪ With the courier", then the note | the same, plain | the same, the title in bold |

- **The summary carries the buttons.** The words go first without buttons, then the summary
  with Yes and Change under it. The read-back is in both: the model isn't told about cards, and
  the yes is to the read-back's words (D107), so they stay; the card is what the buttons confirm.
  Any other card leaves the turn's choices on the words.
- **A tap is words**, as for every button (D99): a callback's data on Telegram (64 bytes, else
  no button), a postback on Messenger.
- If a card fails to send, it's logged; the words have gone already. Text past a button
  template's 640 characters or an interactive message's 1,024 is cut.

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
owner replies /done or "." ─► `active` again: the agent answers, and sees what the team said
owner replies "." to a chat the agent has ─► `human`, with nothing sent to the visitor
```

### The team's side of a chat (D136)

- **Labels:** a list on the conversation in lower case, once each (`set_labels`); the list
  filters by one (`?label=`, matched as `"vip"` in the JSON). Pressing a label in a chat's
  side shows the chats with it.
- **Notes:** their own table, added and never edited (rule 5), with the author's email. They're
  not in `messages`, so the prompt and the visitor never get them.
- **Replying from the inbox:** `POST …/reply` adds the words as the team's, sends them (as a
  Telegram reply would), and takes the chat over (D135). Outside Messenger's or WhatsApp's 24
  hours Meta refuses it; it's logged and stays in the transcript. A call can't take one.
- **Saved replies:** `saved_replies` in every agent's config (up to 50), edited with the owner's
  answers and picked from the reply box. Only the team uses them; the prompt doesn't have them.
- **The model's help (D137):** *Summary*, *Ask about this chat* and *Draft a reply* above the
  reply box. `ConversationService.assist` runs the chat's own prompt and history through the
  same loop with no tools and one last line of what's wanted (`ASSIST`, docs/prompt-rules.md
  §6c); its words come back as `{text}`. Nothing is added to the chat or sent: a draft fills
  the reply box, a summary or an answer shows above it. The turn is saved on the channel
  `assist`, so it's traced and counts as a reply on the plan. A failed model call is a 503.
- **Beside the chat:** a shop's buyer record (D132) from its orders, then what it took, the
  labels and the notes.

### Taking a chat over (D135)

The team takes a chat, and the agent stays quiet in it, three ways: "." from the Page itself
(Business Suite or Messenger), "." in reply to an alert on Telegram, or the inbox's *Take
over* switch (`PUT /api/conversations/{id}/takeover {on}`). Each sets the conversation
`human` and `taken_over_at`; "." again, the switch, or `/done` gives it back.

- **Quiet:** `taken_over_at` is the team's last word (a Business Suite message or a Telegram
  reply moves it on). A visitor's message `TAKEOVER_QUIET_MINUTES` (30) after it gives the
  chat back first, then is answered (`ConversationService._given_back`): checked when they
  write, so no round runs for it. The AI off for a visitor (D127) sets `human` with no
  `taken_over_at`, so it never goes back by itself: it goes back when the AI is on for them
  again and they write.
- **Business Suite's messages:** the Page subscribes `message_echoes`. Every message we send
  carries `metadata: "heydesk"` (`ports/messenger.py`, `OURS`), and so does its echo, so an
  echo without it is the owner's own. Its words are added to the chat as the team's
  (`page_wrote`, rule 5: added, not edited), so the agent reads them next turn; an echo
  before there's a chat, or with no text, is left. A Page connected before this needs
  connecting again for its echoes.
- Given up: WhatsApp's own app (the Cloud API number has none to type in; the inbox switch
  covers it). The buyer sees the "." itself.

- **Telling the buyer (D66, D105):** when the owner moves an order with
  `PATCH /api/shop/orders/{id}`, the row's `news()` gives a line, and
  `TeamService.tell_visitor` adds it to that conversation as the team's (the web page fetches
  it, the bot or the Page sends it, the model sees it next turn; the conversation stays with
  the agent): `confirmed` ("…is confirmed and will be sent out."), `shipped` ("…is with the
  courier. The delivery man calls before he comes.") and `cancelled`; nothing for the other steps. `Services.team` is the `TeamService`, for the routes.

- After a hand-off the agent keeps answering until the owner replies, so no one waits in
  silence. A phone call can't be answered this way; the owner is told to call back.
- The `by` mark is taken off before the transcript goes to the model.

### Paying first (D47–D49, D109)

A shop can set `pay_first`: the visitor pays online through SSLCommerz or
bKash, into the business's **own** account, before anything is placed. The shop sets it per delivery area
(`Zone.pay_first`, D92), as BD shops take the advance outside Dhaka. What's paid is the table's
`Payable.due`, and the payment moves the row to its `PAID_STATUS`:

| Desk | Pays first (`PAY_FOR`) | Then |
|---|---|---|
| Shop | the delivery charge, in an area with `pay_first` | `confirmed`, with no call: the advance is the confirmation (D92); the rest in cash on delivery |

Nothing to pay (free delivery) saves it as before. The owner turns it on and
off, sets the amounts and connects the gateways on the frontend's Agent → Payments section.

**Each business's own accounts (D49, D109).** `payment_accounts` holds one row per gateway
(`provider`): an SSLCommerz store id, store password and test mode, and a bKash account's app
key, app secret, username, password and test mode. `PUT /api/agent/sslcommerz` checks the store
with SSLCommerz first (it opens a ৳10 session no one pays); `PUT /api/agent/bkash` checks the
keys by granting a token. A 400 carries the gateway's reason, and no route returns a secret.
The agent asks for payment only when `pay_first` is on (a shop: in some area,
`ShopConfig.pay_first`) **and** a gateway is connected (`Store.pays_first`): without one, the
prompt says nothing about paying, the tools place orders as before, and `missing` says
"Connect SSLCommerz or bKash…". `services/payments.py` (`PaymentService`, its `PAYABLE`
tables), `api/payments.py` (the pages) and the `PaymentGateway` port, one gateway for each
provider (`Gateways`: `adapters/payments/sslcommerz.py` and `bkash.py`; `PAYMENTS=fake` for
tests) do it. `desks/shared.py` has the tool's answer (`awaiting_payment`) and the prompt's
line (`pay_first_rule`).

```
yes to the read-back ─► the tool saves it `unpaid` (a shop's items held), returns
    pay_link = PUBLIC_URL/pay/<id>; no alert yet
visitor opens the link ─► both connected? a button for each; the visitor picks (?via=…)
    ─► a new session with that gateway for `amount_due` ─► its page
SSLCommerz posts val_id back (the buyer's browser to /pay/<id>/back, and its IPN to
    /api/payments/sslcommerz/<id>)
    ─► settle(id, val_id): the business's store asks SSLCommerz's validation API: VALID,
       for this row (its tran_id), this amount
bKash sends the browser to /pay/<id>/bkash?paymentID=…&status=success (failure, cancel: nothing)
    ─► settle(id, paymentID, "bkash"): the business's bKash account executes it (or asks its
       status): Completed, for this row (its merchantInvoiceNumber), this amount
    ─► UPDATE … WHERE status = 'unpaid' → PAID_STATUS (new; a shop order confirmed), paid,
       payment_ref (only the first post wins)
    ─► "✅ Payment received…" added to the chat as the team's (the page and the model see it;
       the bot sends it on Telegram) and the owner's alert
```

- The agent can't mark anything paid: no tool moves `unpaid`, and the prompt says nothing is placed until a message says so. What the
  browser posts is never trusted; only `val_id` or `paymentID`, asked about with the
  business's own account.
- bKash (Tokenized Checkout, `adapters/payments/bkash.py`) grants a fresh token for each call
  rather than keeping one. It has no server-to-server notice like SSLCommerz's IPN, so its
  return is the one path; bKash takes the money only when we execute, so a buyer who closes
  the tab first isn't charged.
- The link opens a fresh session each time, so it doesn't run out. The row's id is long and
  random: the link is its own key, like the Telegram webhook's channel id.
- The owner can still move `unpaid` → `new` (a shop order: `confirmed`) when it was paid some
  other way, or cancel it.

### Plans and billing (D106, D109)

A business pays HeyDesk for a **plan**: AI replies in any 30 days, and moderators
(`services/billing.py`, `PLANS`). Prices are in taka, before VAT:

| Plan | A month | A year (10 months' price) | Replies in 30 days | Moderators |
|---|---|---|---|---|
| Free trial (14 days, for every new business) | ৳0 | | 200 | 1 |
| Starter | ৳2,490 | ৳24,900 | 3,000 | 1 |
| Growth | ৳5,990 | ৳59,900 | 12,000 | 3 |
| Pro | ৳12,990 | ৳1,29,900 | 35,000 | 10 |

```
signup ─► the trial: plan trial, paid_until = now + TRIAL_DAYS (14)
billing page ─► POST /api/billing/checkout {plan, months, gateway} ─► a subscription_payments row
             (unpaid) ─► our SSLCommerz page (BILLING_STORE_*) or our bKash page
             (BILLING_BKASH_*, D109), not the business's own accounts
gateway sends word back (SSLCommerz: /billing/<id>/back and its notice; bKash: /billing/<id>/bkash
             with status=success) ─► settle: our account says paid, this payment, this amount? ─► paid (once: only from unpaid); the plan is the new one,
             priced again (quote, D116): applied, or held as review if the plan changed meanwhile
every turn ─► visitors.admit ─► plan_problem: now ≥ paid_until (+ GRACE_DAYS (3), not a trial)
             or the turns saved in the last 30 days ≥ the plan's replies ─► 429, the limited line
```

- A **reply** is a saved turn (`turns`, D71): what the model actually cost us. A turn the team
  answers isn't one.
- The plan's moderators cap invites (it replaces D97's `MAX_MODERATORS`).
- `/api/billing`'s `gateways` lists ours that have keys, so the page shows a Pay button for
  each; with none, it can't take payments online.
- An admin sets a plan by hand (`PUT /api/admin/businesses/{id}/plan`), for a payment made to
  us another way; it goes in the audit log.
- The dev workspace is Pro for ten years, so the dev chat and the demo never stop.
- **Changing plans (D116).** The plan is prepaid: nothing renews by itself, and not renewing is
  how an owner cancels (no refunds). `quote` prices every purchase, and `GET /api/billing/quote`
  shows it before the Pay button:

  | Where the business stands | Buying | Costs | The plan after |
  |---|---|---|---|
  | On the trial, or the paid plan has ended | any plan | its full price | that plan, from now |
  | A plan running | the same plan | its full price | the days added after the days left |
  | A plan running | a bigger plan | the difference for the days left, at the rate of the cycle paid last (`workspaces.months`), whole taka, at least ৳10 | the bigger plan now, ending the same day |
  | A plan running | a smaller plan | refused until it ends; the grace days keep answering meanwhile | |
  | More moderators than the plan has | that plan | refused: remove some first | |

  `settle` prices the payment again when the gateway says it's paid, under a row lock on the
  workspace. If it's still the same kind and costs no more than was paid, it's applied;
  otherwise (another payment or an admin changed the plan meanwhile) it's kept as `review`,
  money taken but not applied, and an admin settles it by hand. `/api/billing` lists the
  payments (`paid`, `review`), newest first.
- Both gateways' returns send the owner on to the billing page in the same tab,
  `/billing?payment=paid|review|failed` (303). In development, Vite forwards `/billing/` to the
  backend, as it does `/pay/`.
- Nothing is emailed when a plan is about to end: the owner sees it on the billing page.

### How a shop order is confirmed (D92)

Each order is confirmed **one** way, as Bangladeshi f-commerce shops do it:

| The order | Confirmed by | It's saved | The buyer hears |
|---|---|---|---|
| Its area has `pay_first` (and a store is connected) | the advance: the delivery charge paid online | `unpaid`, then `confirmed` by the payment | "Payment received… your order is confirmed; pay the rest to the delivery man" |
| Cash on delivery, `call_to_confirm` on (the default) | the shop's call, by hand (`calls`, D89, is off for now: D108) | `new`, then `confirmed` after the call | "The shop will call … to confirm it before sending it" |
| Cash on delivery, `call_to_confirm` off | nothing | `confirmed` at once, for the owner to send (D95) | "It's confirmed and will be sent out" |

A paid order is never queued for a call: the queue takes only `new` orders. `calls.enabled`
needs `call_to_confirm` (a 422 otherwise).

### Orders the team types, and the end of an order (D131)

- **A buyer who called:** *New order* on the inbox posts `/api/shop/orders`. `checked_order`
  (`desks/shop/tools.py`, the product page's too) prices it and checks the stock, the area and
  the mobile; `ShopStore.add_team_order` saves it `confirmed`, in no chat. It takes a plain
  `NewOrder`, not a `Confirmed`: the team member who typed it is the yes (rule 2's one
  exception, and the only writing method without one). The owner's alert says "typed by the
  team"; the audit log has who.
- **More steps:** `on_hold` (waiting on the buyer or the shop: the courier doesn't book it;
  the buyer hears nothing) and `packed` (ready for the courier, which books it as it does a
  confirmed one). Both hold stock, can be changed and cancelled, and are on-hold and
  processing in Woo. The buyer's tracking card has *Packed* between Confirmed and With the
  courier.
- **The end:** a delivered or returned order takes `paid_in` (the rider's cash reached the
  shop), a returned or cancelled one a `reason` (the screen offers refused, damaged, wrong item,
  no answer, changed their mind, or the owner's words). Before it ends, either is a 409.

### The owner's dashboard (D144)

`GET /api/stats` adds up rows we already keep, in `Store.stats(start, end)`; the route asks for
today, yesterday and the last `days` days in the agent's `timezone`. **Chats** are the
conversations with a turn in the stretch, and **handed off** those with a turn that handed off;
the rest the agent handled alone. **Sales** are the rows of the desk's `SALES` tables (the shop's
orders) made in it and not `cancelled` or `unpaid`; their **amount** adds each row's `total`. **Bought** counts the chats those sales belong to. A shop order's
`source` says where it came from: the chat, the product page's *Buy now*, or the team. **Leads**
are the call backs asked for in it that aren't `sold`. The rows are read and counted in Python,
as the admin's overview is (D72).

### The same order twice (D146)

`place_order`, once every detail is valid and before the read-back, asks
`ShopStore.same_order` for an order from the same mobile in the last `DUPLICATE_HOURS` (6),
not cancelled, with the same items (code, size and colour; the quantity may differ). If there
is one, the tool fails with a hint: ask whether it's a second order on purpose. A yes is
`again=true` on the next call, which skips the check. The product page's *Buy now* and the
team's orders aren't checked.

### Coupons (D133)

A buyer says a code; the model passes it to `update_cart` as `coupon`. Python finds it
(`ShopStore.coupon`, in capitals), checks it hasn't ended or been used up (`Coupon.problem`),
and works out the discount (`Coupon.discount`: a percent of the items, floored to the paisa;
an amount, at most the items; or none, with the delivery charge 0). The cart keeps it
(`{coupon: {code, kind, value}, discount}`) until another code is given, so later calls needn't
repeat it. A bad code is `ok=False` with `coupon_problem`, and the cart is kept without it.

- **On the bill:** `cart_text` adds "coupon EID10 −৳95" (or ": free delivery"), so the state
  note, the read-back and the owner's alert all carry it; the summary card has a coupon line;
  `NewOrder.total` is subtotal − discount + delivery charge. `money()` writes −৳95.
- **Counted once:** saving the order adds a use in the same transaction as the stock, after
  checking it again; ended or used up since is `CouponGone`, and nothing is saved.
- **Elsewhere:** Woo gets the discount as a negative fee line; a changed order keeps the
  discount's amount (at most its new items). Team orders and the product page take none.
- Given up: a cancelled order doesn't give its use back; no minimum order; one code an order.

**An offer to everyone who wrote today (D140).** `POST /api/shop/offers {text, kind, value,
ends_at, within_hours}` sends the owner's words, as they are, to each shop chat with a turn in
the last `within_hours` (24 by default, up to a week, D163; not a call, not an `assist`),
through `tell_in_window`, and saves it as a coupon
`OFFER1`, `OFFER2`… whose `only_for` is the chats it reached. `ShopStore.coupon` gives such a
code only to those chats, so said by anyone else it's "no coupon"; `update_cart` uses the
newest offer still running for the chat when the buyer gave no code and the cart has none.
From there it is a coupon: on the bill, checked again at the save, and gone at `ends_at`.
- Given up: the buyer's own code replaces the offer (one code an order); no screen for past
  offers beyond the coupons list.

### A buyer's record (D132)

Before an order is sent, the team sees what its mobile did with this shop: `ShopStore.records`
counts each mobile's orders by how they ended, in one grouped query for a whole page, and
every order out of the API carries it as `buyer` ("3 orders · 1 returned", the trouble in red;
"First order from this mobile"). `/api/shop/buyers` lists the shop's mobiles, the latest to
order first (by order number), with the name on their latest order, what their delivered
orders came to, and the chats they ordered from. Only this shop's orders count (rule 6).

- Given up: the courier's own fraud record. Steadfast's public API has no fraud check; the
  packages that offer one log in to its merchant website with the owner's password and read
  the page, which breaks when the page changes. Pathao has none either. The buyers list is
  the shop's only.

### The courier (D112, D113)

A confirmed order goes to the courier on its own. `CourierApi` (`ports/courier.py`) has
`check(account)`, `book(account, Parcel) → parcel id` and `status(account, parcel id) →
Tracked(said, ending)`. Its adapters: `Steadfast` and `Pathao` (`adapters/courier/`), with the
shop's own merchant login linked at `/api/agent/courier` (checked when saved; `courier_links`,
secrets encrypted), and `FakeCourier`, which moves a parcel one step every 2 minutes and
delivers it after 8. A shop with no link uses the fake one, except in production (by hand).
`CourierService.run`, every `COURIER_SYNC_MINUTES`, for each shop with a parcel to book or follow:

1. Each `confirmed` or `packed` order with no `parcel_id` is booked (one `on_hold` waits, D131) (the cash still owed in whole taka,
   the order code as the invoice): it's `shipped`, the buyer hears so, the owner is told to pack it.
2. Each `shipped` order with a parcel is asked about: the words go in `courier_status`
   (`track_order` gives them), and `delivered` or `returned` moves the order; the buyer hears
   a delivery, the owner a return.

3. On the way (D134): when a shipped parcel's words say it's out for delivery (only the fake
   courier's "Out for delivery" is known yet), the buyer hears once that it comes today and the
   cash to keep ready (`reminded`).
4. A review (D134), at the start of every round, also for a shop that books by hand:
   `REVIEW_AFTER_HOURS` (24) after an order from a chat was delivered, its buyer is asked "How
   was your order O-101? Tap 👍 or 👎, or write to us." with those two buttons, once
   (`review_asked`); one due more than two days ago is only marked. A tap is the buyer's next
   message, answered by the agent.

Both go through `TeamService.tell_in_window`: on Messenger and WhatsApp only within 24 hours
of the buyer's last turn (Meta refuses a business's message after it), on Telegram and the web
chat always, on a call never. Outside it nothing is sent, and it isn't tried again.

A `CourierError` stops that shop's round: it's noted on the link (`problem`), the owner hears
it once, and it's tried again next time. In production a shop with no courier linked moves its
orders by hand (D95).

## 10. Voice (M2)

A `voice/` package turns speech into `start_turn(channel="voice")` calls and
the turn's events back into speech, with Pipecat (D18): the browser's audio over
WebRTC (`POST /api/voice/offer`), Silero VAD, Deepgram for speech-to-text
(streamed; `STT_PROVIDER=chorus` switches to Chorus, one upload per utterance
after 0.5s of silence, D39; `STT_PROVIDER=whisper` to our Whisper, D87) and Piper for
text-to-speech.

- **Off for now (D108, D164):** `Desk.takes_voice`, like `takes_photos`, is off for the shop, so
  `GET /api/chat/{key}` says `voice: false`, the page shows no Call button, and
  `POST /api/voice/offer` is a 403 `no_voice`. The code stays for the shop's confirmation
  calls (below): turning it on is the one switch.
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

**Off for now (D108):** the shop takes no voice, so no call is queued for an order and the
owner's "Call to confirm" is a 409, the same refusal as production's (D105). The code below
stays, unused, for when a shop takes calls again.

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
"yes" ─► confirm_order: the line is the read-back (`state.read_back`) ─► `new` → `confirmed`, an alert
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

- Unit tests for `runtime/` and `domain/` with `FakeLLM` and the fixed clock
  (`FIXED_NOW` = Tue 29 Sep 2026 08:00 UTC = 14:00 in Dhaka).
- A test file per desk (`tests/integration/test_shop.py`, and `test_messages.py` for the shared
  tools): its tools against its real store on in-memory SQLite, its prompt, its routes, and
  whole conversations scripted with `FakeLLM` (an order saved only after a yes). The `world` and `client` fixtures are in `tests/conftest.py`.
- Live runs against Groq only with `-m live`, and only when asked: they cost quota.

### CI and deploying

`.github/workflows/ci.yml` runs on every pull request and every push to `main`:

| Job | What it proves |
|---|---|
| `check` | `scripts/check.sh`: tests, lint, format, types, import rules |
| `postgres` | `alembic upgrade head` and `alembic check` on a real Postgres 17: every migration runs there, and no model change lacks one |
| `audit` | `pip-audit` finds no known hole in the server's dependencies |
| `image` | the Docker image builds and answers `/health` with the fake providers |
| `deploy` | only on a push to `main` (a merged PR), after all four: Render's deploy hook with this commit, then `/health` until it names this commit |

Production is Render (`render.yaml`, a Blueprint): one web service from the `Dockerfile` and a
Render Postgres in Singapore. Render's own auto-deploy is off, so nothing goes live that CI didn't
pass. The image runs `alembic upgrade head` on start. `DATABASE_URL` takes Render's
`postgresql://…` as given; `Settings` names the asyncpg driver. Nothing lives on the server's
disk, which a deploy wipes: Qdrant is Qdrant Cloud, photos are in R2. One instance only, because
the call queue is in its memory. Voice goes through the
Cloudflare TURN relay, since Render takes no UDP. The frontend deploys from its own repo.

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
| A shop's search that knows Banglish ("lal" for Red) or meanings | The whole list is in the prompt, every turn; fine for the few dozen a small business has (D41). A shop over `LONG_LIST` (60) products, such as a linked WooCommerce store, lists only its groups, and `find_product` matches plain words (D80) |
| Knowing a returning visitor on a shared computer | The web chat and a call use the browser's token as the `contact` (D114), so someone else on the same browser is offered the last visitor's name, phone and address, and gets their notes (D124). Clearing the browser's data makes a new visitor |
| Several tool calls in one Gigalogy reply | The first `{"tool": …}` runs; the model sees its result and can call the next |
| Progress-line policy (thresholds, rotation) | Web shows the tool's label; voice says the tool's filler |
| The model calling a writing tool again after a "maybe" | The model judges the yes (D107). Python only makes sure the details are the ones last read back, so a wrong call saves what the visitor heard, never something else |
| What the caller *heard* after a barge-in | The turn's trace says `interrupted`; the text is what was generated |
| Clearing old `turns`, a price for Gigalogy, Gemini embeddings and speech-to-text | Every turn is kept; a turn's cost is Groq's alone (Gigalogy reports tokens, no price), so a Gigalogy turn's cost is 0, meaning unknown (D71) |
| Usage shown to the owner, limits per plan, Cloudflare Turnstile | One `VISITOR_DAILY_TURNS` and `WORKSPACE_DAILY_TURNS` for every business (D51); cost is in each turn's log line. Clearing the browser's token makes a new web visitor, still inside the business's daily turns and the IP's rate limit (D52) |
| Bangla voice: a GPU, streaming the audio inside a sentence, several calls speaking at once, English words in a Bangla reply, Bangla on Chorus | One sentence at a time, whole, on the CPU or a Kaggle T4 for demos (D85); a second call waits for the first's sentence. The local VITS voice drops Latin letters, so the prompt asks for them in Bangla letters. Chorus is sent `bn`, untested (D84) |
| Teams: more than one owner, other roles or per-page permissions, one email in two businesses, handing the business to someone else, the owner seeing an admin's changes, a 2FA | One owner and up to 10 moderators per business, one fixed split (D97); an invited email can't already have an account; the activity has the routes, not what changed in them |
| Billing, phone numbers, hosting | No plans or billing; voice in the browser only, and off for now (D108, D164); a Docker image and `compose.yaml` run it on one machine (SQLite in a volume); production is one Render instance with Postgres (D161), no staging, and a rollback is Render's *Rollback* button |
| Telegram: voice notes, messages over 4,096 characters, buttons the model chooses, a delivery-area button, sharing the phone with a tap | Text, photos (D63) and Python's buttons (D99), and replies are a few sentences (D42). The district comes from the address (D98), so there's no Inside/Outside Dhaka button; Telegram's contact button and Messenger's phone quick reply are a later step (docs/product-gaps.md §2) |
| Cards kept on the message, a card on a reloaded chat or in the owner's view | A card is shown once, when its tool runs; the words say the same (D110) |
| Owner takeover: a chat the owner never gives back with `/done` | It stays with the team until it's quiet for `TAKEOVER_QUIET_MINUTES` (D135); after `CONVERSATION_IDLE_HOURS` the agent starts afresh in the same chat (D157) |
| Telegram: a repeated update (Telegram retries when we don't answer) | The webhook says OK before the turn runs, so a retry is rare; one would be answered twice |
| Ending logins when a password changes | A login token lasts 7 days, and a new password (changed, or reset with the emailed code, D93) doesn't end the ones already made. An admin's role is checked against the database on every request (D23) |
| Changing `SECRETS_KEY` | It's set once; the secrets saved with the old key can't be read with a new one (D74) |
| Shop: Instagram | Web chat, voice, Telegram, Messenger and WhatsApp for now (D45, D60, D103) |
| WhatsApp: Embedded Signup to connect a number, its `wa.me` link, templates (a message after the 24-hour window), voice notes | The owner pastes a system user's token and the two ids from Meta's settings; they know their own number. Text, photos and Python's buttons (D103) |
| Messenger: Facebook Login to connect a Page, the Get Started button, a reply after the 24-hour window, the Page's own replies from its inbox | The owner pastes a Page access token made in our Meta app; a visitor's first message starts the chat; text, photos (D62) and Python's buttons (D99). A reply after 24 hours fails and is logged. The agent doesn't see what a person wrote in the Page's inbox (D60) |
| Photos: several in one message, a photo sent as a file on Telegram, deleting a photo's file when the owner removes it | Web chat, Messenger (D62) and Telegram (D63), one photo a message, the shop only; a removed photo's file and vector stay, and search skips it (D57) |
| Photos: who can see one | A photo's URL is public but has a random id, like the chat link; a visitor's photo is kept for the owner's view (D57) |
| Import: saving a business's column match, one column split into two fields ("Size/Colour"), a limit on suggest calls, Shopify or a POS | The screen asks again on each upload (the suggestion makes that quick); the owner splits the column in the sheet; CSV only for now (D53). Matching by what a column holds (a column of links is `photos`) waits until names miss (D115) |
| Linked sheet: writing the stock back to the sheet, a private sheet (a Google service account), checking the stock again just before an order is saved, Excel on OneDrive | The sheet is read only; the owner updates a count when they restock or recount, and an order packed but not yet marked shipped is taken off once more (mark it shipped first). Shared by link, so the owner keeps private columns elsewhere. The list is at most `SHEET_SYNC_MINUTES` old. Sheets only (D58). A sync and an order at the same moment take turns (D162) |
| WooCommerce: reading a webhook's body (it only starts a sync), a store on plain HTTP or with plain permalinks, reading back a change made to our order in Woo's admin, a returned order's stock, a variation's own photo (the product's are taken, D102), attributes other than size and colour as variants, a store with thousands of products, Shopify | A webhook starts a whole sync, which is simple and catches everything; HTTPS with `/wp-json/` only, which nearly every store has; HeyDesk is where our orders move; a returned order is left as it is in Woo and the owner restocks there; a variation's photo is rarely different enough to matter; other attributes are a line in the description; a sync is one call per 100 products plus one per variable product; Woo first (D75) |
| Website chat: only the business's own websites may show its bubble, removing a logo's file | Anyone can paste a business's line, as anyone can share its chat link; the daily limits (D51) and rate limits (D52) keep the cost small. A replaced logo's file stays (D82) |
| Confirmation calls: a phone carrier, who may open `/phone` for a number, a call per business at once | No carrier is planned: the page on a phone is the phone. Dev only, like the Call button: anyone who opens `/phone` with a mobile gets that mobile's calls, which say the order's code and total. In production, which serves no `/phone`, no call is queued and the owner's "Call to confirm" is a 409, so a call is never only missed (D105). One call at a time for the whole server. The shop only (D88, D89), and off while the shop takes no voice (D108) |
| Payments: refunds, an unpaid order that expires | The owner refunds in the gateway's panel (a payment for a cancelled one is an alert saying so); the owner cancels what was never paid |
