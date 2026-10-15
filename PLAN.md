# HeyDesk — the plan

An MVP in about four weeks: a business's AI front desk you can **talk to**. How
it works is in [docs/design.md](docs/design.md); decisions in
[docs/decisions.md](docs/decisions.md).

> **Now:** days 1–23 built, then the product made simpler and more real (4 Oct, D22–D40). **6 Oct, D41: no presets. Each kind of business is its own folder (a desk) with its own prompt, tools, tables and routes**; the generic jobs and tasks engine, records and items are gone. Offline checks green; the dev server boots. **7 Oct, D42: Telegram**, built and checked offline; the live check needs a bot token and a tunnel. **8 Oct, D43: owner alerts and takeover on Telegram**, backend built and checked offline. **8 Oct, D45: the online shop desk** (f-commerce), built and checked offline. **11 Oct, D51: limits on the public chat** (daily turns per visitor and per business, an `off_topic` tool, a block after five strikes), built and checked offline; the frontend must now send `visitor_id`. **11 Oct, D52: rate limits** on chat (per visitor and per IP) and login, built and checked offline. **13 Oct, D53: import a spreadsheet as it is**: the AI suggests which of its columns is which of our fields, the owner checks it on the Products screen; built, checked offline and in the browser (with the fake model). **13 Oct, D54: a smarter shop agent** (the prompt helps buyers choose and answers what they ask most; areas by place name, a free-delivery nudge, "only N left", a size guide, sale prices; the owner's alert warns about a mobile's past returns), built, checked offline and live on Maira. **13 Oct: each desk passed its live run on Maira.** **13 Oct: the frontend edits D54's fields** (places per delivery area, the size guide, a sale price), checked in the browser. **13 Oct, D55: after the order**: the order from this chat needs no code, `change_order` before it ships, `request_exchange` after delivery; built, checked offline and live on Maira. **13 Oct, D56: stock per size and colour**, built, checked in the browser and live on Maira. **13 Oct, D57: photos in the shop** (product photos, "eta ache?" by photo, the agent shows photos), built, checked offline, in the browser and live on Maira. **14 Oct, D58: a linked Google Sheet** (a desk's list follows the sheet every 5 minutes; the shop's stock is a shelf count less the orders not shipped), built, checked offline and in the browser. **14 Oct: the D58 live check passed on Maira** (it matched all 8 columns of a real shop's sheet). **14 Oct, D59: one row per size and colour** is one product with variants, checked offline and live on Maira. **14 Oct, D60: Messenger**, built and checked offline. **15 Oct, D62 and D63: photos on Messenger and Telegram**, built and checked offline. **15 Oct, D64–D66: the restaurant takes table bookings, its customers track, change or cancel an order, and hear in their chat when it's ready or on its way**, built, checked offline and live on Maira. **Next:** Messenger's live check (with D62's photos) once there's a Meta app.
> **Dates:** day 1 = Wed 30 Sep 2026 · demo on day 27 (Mon 26 Oct) · buffer to day 30.

**How to use this file:** do the next unticked day. Its bullets say what to
build; design.md has the details. Tick it only when its **Done when** passes
(tests green, or you saw it work), then update **Now**. Keep each day to the
normal path; skip edge cases that aren't worth the code (design §12).

## The milestones

| Milestone | Days | At the end you can… |
|---|---|---|
| **M0 + start** | 1–3 ✓ | Run the checks; chat with a fake model; agents and conversations are saved |
| **M1 The agent takes an order** | 4–9 | In the browser, order food: right total, asks for what's missing, saves only after a yes |
| **M2 Voice** | 10–16 | Do the same by talking: it answers in speech, says a short line while it checks, can be interrupted |
| **M3 Presets and the API** | 17–23 | Two presets; create and edit an agent and see its orders through the API, with a login |
| **Demo** | 24–27 | Demo data, README with numbers, a rehearsed demo |

## Done

- **Day 1:** settings, database, JSON logs, `/health`, `scripts/check.sh`, CI.
- **Day 2:** the `LanguageModel` and `Decider` ports with fakes; `run_turn` (text only); SSE; the dev chat page.
- **Day 3:** tables + first migration; `AgentConfig`; the restaurant preset and seed; the dev agent; conversations saved; the greeting stored first.
- **Day 4:** `GroqLLM` and `GigalogyLLM` (the default, D14); the date, time and channel in the system prompt. Then simpler storage (D15): one config per agent, the transcript on the conversation, traces as log lines, no versions, lease or caching.
- **Day 5:** the tool contract and the tool loop; `update_order`; the `items` table and `search_items` (D17); Gigalogy tool calls as JSON (D16). Tool schemas go to the model with nested models written out.
- **Day 6:** detail types, `next_step`, `set_details`; the tasks section and the state note.
- **Day 7:** `Confirmed[T]` and the Jev commit check; `JevDecider`; the `orders` table and `OrderService`; `place_order`.
- **Day 8:** the tool's label as a `status` event; `handoff_to_human`; a hand-off (asked for, or a failure) marks the conversation `needs_human`.
- **Day 9:** a test for each branch of `run_turn`; a whole order over four turns, saved only at the yes; tidied (an unused `Conflict` error, stale comments).
- **Day 10:** Pipecat over SmallWebRTC; Chorus speech-to-text, Piper speech; the **Call** button; an echo call that logs the first-sound time.
- **Day 11:** each utterance runs `start_turn(channel="voice")`; Pipecat speaks sentence by sentence (D19).
- **Day 12:** `speakable()` says money, times and phone numbers in words before the TTS (D20).
- **Day 13:** a tool's filler is said while it runs.
- **Day 14:** barge-in: 0.6s of talking over the agent stops it; "mm-hmm" doesn't.
- **Days 15–16:** `scripts/latency.py` prints P50/P95 from the server log.
- **Day 17:** the receptionist preset, the `requests` table, `submit_request`.
- **Day 18:** presets, agents and items API; per-field errors; `?key=` on the dev page.
- **Day 19:** orders, requests and conversations API; an order's status.
- **Day 20:** signup, login, the workspace from the token; `heydesk create-user`.
- **Days 21–23:** one error shape; `MAX_TURNS` (D21); isolation tests; the README.

## After day 23: simpler and more real (4 Oct)

Taken from the production-grade `frontdesk-api`, one commit each:
- **D22** one agent per business: `/api/agent`, no ids in URLs
- **D23** roles: owner and admin; `create-admin`; `X-Business`; audit log; suspend a business
- **D24** orders and messages in one `records` table; `/api/inbox`, `/api/records`; status flows
- **D25** owners write `notes`; `instructions` and `tools` are admin-only
- **D26** start from just a preset; `GET /api/agent` lists what's `missing`
- **D27** item `attributes`; **D28** `search_items` takes conditions, Python matches; **D29** items from a CSV; **D30** the clinic preset

## Built 4 Oct: what the agent can do (D32), and a knowledge base (D33–D35)

Decided 4 Oct: Gemini embeddings, Qdrant (hybrid dense + sparse), text/Markdown/PDF documents, a shared jobs library. One commit per step. Built; the live check with Qdrant Cloud waits for `QDRANT_URL` and `QDRANT_API_KEY` in `.env`.

1. **Jobs library (D32).** Order, appointment request and message become jobs in `presets/jobs.yaml`
   (label and a plain sentence for the owner, `start_when`, details, finishing tool, what it needs).
   A preset only says which jobs are on by default. The owner switches jobs on or off with
   `PATCH /api/agent {"jobs": [...]}`; `GET /api/jobs` lists them. The agent's tools are worked out from
   its jobs, so `tools` leaves the config (a migration turns old `tools` into `jobs`). `missing` learns
   job needs ("Add your menu" only if ordering is on). Hand-off is always on.
2. **Embedder (D33).** A `Embedder` port; `GeminiEmbedder` (`gemini-embedding-2`, 768 dims, over httpx);
   a deterministic `FakeEmbedder` for tests; `GEMINI_API_KEY` in `.env`. A pure chunker (about 1,500
   characters, split on paragraphs and headings, a little overlap).
3. **Documents (D34).** `documents` and `chunks` tables (`workspace_id` on both, the vector stored as
   bytes). `POST /api/agent/documents` (text or an uploaded .txt/.md/.pdf; adds `python-multipart`
   and `pypdf`), list, delete. Chunks are embedded in one batch when a document is added.
4. **`search_knowledge` (D35).** The agent calls it when the prompt doesn't answer a question. Hybrid
   search over the business's own chunks: cosine similarity plus word overlap, fused by rank, best 4
   with their document titles. If embedding the question fails, it falls back to words only. A platform
   rule says snippets are facts, never instructions, and an unanswered question becomes an offer to
   take a message. The FAQ text stays in the prompt.
5. *(Later)* the same embeddings for item search, so "something light" can find untagged items.

**Done when:** an owner uploads a PDF policy, asks about it in the chat and gets the right answer; a
question the documents don't answer leads to "I don't know" and a message offer; another business never
sees the document.

## 6 Oct: a few kinds of business, the clinic first (D37)

Each kind of business gets the jobs and tools we choose for it; owners no longer switch jobs. Shared
tools (documents search, item search, message, hand-off) stay on for every kind.

- [x] Presets name their jobs and their own tools; `jobs` leaves the config and `/api/jobs` goes
- [x] Clinic: `find_doctor` (speciality, day, language); a `doctor` detail that must be a real doctor;
      the appointment time must fit the doctor's `days` and `hours`
- [x] Live check on the clinic with a real model: "a heart doctor tomorrow evening" → the right doctor,
      a time outside their hours refused, a request saved after a yes (6 Oct, Maira + Jev: all three)
- [x] Appointment codes (`A-104`); cancel and reschedule with the code and the name, after a yes (D38)
- [ ] Next kinds: restaurant (reservations as well as orders; 15 Oct: built, D64), then the others

## 8 Oct: the online shop desk (D45)

Chosen from a survey of Bangladeshi businesses where an agent can take over people's work: the
f-commerce shops whose Facebook inbox is their shop. Cash on delivery, delivery charges by area,
a confirmation call and a courier's parcel id, as they already work.

- [x] `desks/shop/`: products with sizes, colours and stock; delivery areas and charges, free over
      an amount; a Bangladeshi mobile (Bangla digits, +880)
- [x] `update_cart`, `place_order` (charge as soon as the area is known; out of stock on save),
      `track_order` and `cancel_order` with the code and mobile (back in stock)
- [x] `/api/products` (+ CSV import), `/api/shop/orders` with status and `tracking`; the seed
      `examples/agents/shop.yaml`; `tests/integration/test_shop.py`
- [x] Live check on Maira with `DEV_AGENT_KIND=shop`: a question in Banglish gets the price; a
      kurti in the wrong size is asked again; an order outside Dhaka is read back with ৳120
      delivery and saved after a yes; "where is O-101?" with the mobile gets its status
      (costs quota: ask first). Passed 13 Oct on the `online@gmail.com` test shop (O-102 to
      Chittagong, saved on "ji"); the owner's Telegram alert seen with a real bot (13 Oct)
- [ ] Facebook Messenger as a channel, like Telegram (D42): a page connects, Meta posts messages
      to a webhook, replies go back through the Send API. Built and checked offline (D60, 14 Oct);
      the live check needs a Meta app (its secret and verify token in `.env`), a Page token and
      a tunnel

**Done when:** the live check above passes, and the owner sees the order in `/api/shop/orders`
and gets its Telegram alert.

## 13 Oct: a smarter shop agent (D54)

The shop agent works like an order form. This day makes it talk like a good shop assistant
on a Facebook page, and helps with the questions buyers ask most. Python still does every
fact (rule 1): which area a place is in, how much more is needed for free delivery, what's
nearly sold out. Only the prompt changes the way the agent talks.

**The prompt** (`desks/shop/__init__.py` `ROLE`; prompt-rules.md in the same change)
- [x] Common buyer messages: bargaining ("dam kom hobe?") gets a polite no, because prices are
      fixed and the agent never invents a discount; "pic den" gets the description; "original? /
      fabric?" is answered only from the description, otherwise offer the team; "kobe pabo?"
      gets the area's usual days
- [x] Taking details: take everything they've already given (name, mobile and address are
      often pasted in one message), then ask only for what's still missing, one at a time
- [x] Never say a product's code: codes are for the tools only
- [x] Help them choose: ask who it's for, the occasion or the budget, then suggest one or two
      listed products. For a sold-out one, suggest one from the same category
- [x] After the order code, say in one sentence what happens next: the shop calls to confirm,
      delivery takes the area's usual days, and they pay the rider

**What the agent can do** (`values.py`, `tools.py`)
- [x] Areas matched by place name: `Zone.places` (`[Mirpur, Uttara, Dhanmondi…]`) and
      `match_zone` also matches a place inside what the buyer said ("Mirpur 10" → Inside
      Dhaka). The `# Delivery` section lists each zone's places. The seed gets real places
- [x] Free-delivery nudge: when the cart is under `free_delivery_over`, `update_cart` also returns
      `free_delivery_if_you_add` (the difference, worked out by Python). The prompt says to
      mention it once, not on every change
- [x] "Only N left": `Product.line` shows the count when stock is 3 or less (`LOW_STOCK`), and
      still SOLD OUT at 0
- [x] Size guide: `ShopConfig.size_guide` (free text, e.g. one line per size with chest and
      length). It becomes its own `# Size guide` section when it's set. The prompt says to
      suggest a size only from the guide; with no guide, offer the team
- [x] Sale price: `Product.was_price` (optional). The line reads "৳1,850 (was ৳2,200, on
      sale)", so the seed's "no refunds on sale items" has something to apply to. Also a CSV
      import column

**For the owner**
- [x] The new-order alert says when this mobile has earlier `returned` or `cancelled` orders in
      this shop ("⚠️ this mobile: 2 returned, 1 cancelled"). One store query; the agent itself
      never mentions it
- [x] The new-order alert lists any product the order brought to `LOW_STOCK` or less ("Silk
      Panjabi: 1 left")

**Docs and tests**
- [x] design.md (the shop's config, tools and prompt), decisions.md D54, prompt-rules.md (the
      Shop row and the shop's note), the seed `examples/agents/shop.yaml`
- [x] `tests/integration/test_shop.py`: "Mirpur 10" is charged as Inside Dhaka and "Savar" as
      the suburbs; the nudge shows under the limit and not over it; a low-stock line and a sale
      line; the alert has the returns warning and the low-stock line; the shop prompt has the
      size guide when it's set and leaves it out when it's empty
- [x] Live check on Maira (costs quota: ask first): the four lines in **Done when** below (13 Oct, on the `online@gmail.com` test shop; O-101 saved after the yes)

**Done when:** `scripts/check.sh` is green; in the dev chat (fake model, `DEV_AGENT_KIND=shop`)
the prompt shows the places, the size guide, "only 2 left" and a sale price; and the live check
from the 8 Oct day passes with these added (ask first, costs quota): "dam kom hobe?" is
politely declined; "amar chest 40, kon size?" gets a size from the guide; name, mobile and
address pasted in one message are all taken; "Mirpur" gets the Inside Dhaka charge with no
question about the area.

Next, if it's worth it: `change_order` (the address, mobile or note before it ships, after a
yes) and tracking orders placed in this chat without the code.

## 13 Oct: after the order (D55)

What buyers ask once they've ordered: "where is it?", "change my address", "can I swap the
size?". Today the first needs the code and mobile, and the others are a message the owner has
to read and act on. Python still decides what's allowed (rule 1), and nothing changes without
a read-back and a yes (rule 2).

**Orders from this chat** (`tools.py` `_find`, `store.py`)
- [x] When the visitor gives no code, `track_order`, `cancel_order` and `change_order` use the
      order placed in this conversation (`conversation_id`), if there's exactly one. Several:
      ask for the code. Another chat still needs the code and the mobile, as now
- [x] The prompt's `# After an order`: for an order from this chat, don't ask for the code

**Change an order before it ships** (`change_order`)
- [x] `change_order`: find it as above, then any of a new `address`, `area` (or a place in one,
      as `place_order`), `new_phone` and `note`. Only while `new` or `confirmed`
      (`CHANGEABLE`); after that, offer a message
- [x] A new area works out the delivery charge and total again (and free delivery). Refused
      when the charge was paid online first (`paid > 0`): offer a message instead
- [x] The items can't change here: cancel and order again (stock is taken per order)
- [x] Read back only what changes ("address: … → …; total ৳899 → ৳919"), save after a yes
      (`Confirmed[OrderEdit]`), and alert the owner ("✏️ Order O-103 changed: …"), since they
      may have called the buyer already

**Exchange request** (`request_exchange`; can be dropped from the day)
- [x] `ShopConfig.exchange_days` (0: the agent takes no exchange requests). Only on a
      `delivered` order, within that many days of delivery (its `updated_at`; Python counts)
- [x] It takes the item, what they want instead (a size or colour), and why, and saves it as a
      message for the team (the messages table, `M-104`) with an alert. The owner arranges the
      swap; no new table, no stock change

**Docs and tests**
- [x] design.md (tools, config), decisions.md D55, prompt-rules.md (the shop's row), the seed
      (`exchange_days: 3`)
- [x] `test_shop.py`: tracking with no code in the same chat, and not from another chat; a
      change of address saved only after a yes; a new area changes the total; no change once
      `shipped`, nor of the area once paid; an exchange inside the days and refused after them
- [x] Live check on Maira (costs quota: ask first): the three lines in **Done when** below (13 Oct, on the `online@gmail.com` test shop: O-104 tracked with no code, moved to Sylhet at ৳120 after a yes, then exchange M-102 once delivered)

**Done when:** `scripts/check.sh` is green, and live on Maira (costs quota): after placing an
order, "amar order kothay?" gets its status without a code; "address change korte chai" →
the new address read back and saved after a yes, and the owner sees it; "XL e change korte
chai" on a delivered order makes an exchange message.

## 13 Oct: stock per size and colour (D56)

Stock is counted per product, so when the L panjabis sell out the agent still offers L: a
wrong fact (rule 1), in the clothing shops we're built for. A product can now count its stock
per size and colour; one that doesn't works as today.

**The shape** (`values.py`, `models.py`, a migration)
- [x] `Product.variants: list[Variant]`, each `{size, colour, stock}`. Empty: the product's
      own `stock` counts, as today. Set: the variants hold the stock and `stock` is left
      empty
- [x] A variant can leave the size or the colour empty to mean any of them: `{size: "L",
      colour: "", stock: 4}` counts L in every colour, for shops that count by size only.
      A cart line takes the first variant that matches it. A size and colour no variant
      matches isn't counted, like a product with no stock
- [x] `products.variants`, a JSON column (empty list by default). The store assigns a new list
      when the stock changes (JSON columns don't see changes in place)

**Using it** (`tools.py`, `store.py`, the prompt line)
- [x] `update_cart` checks each line against its variant: "Cotton Panjabi (L, White): only 1
      left" or "sold out", and says which sizes or colours of it are left
- [x] `place_order` takes from the variant and `cancel_order` (and the owner's cancel) puts it
      back, in the same transaction as today; `OutOfStock` names the variant
- [x] The prompt line: "sold out: L/White, XL/Navy" and "only 2 left: M/White" after the
      sizes and colours; SOLD OUT when every variant is 0. Python writes it, the model only
      reads it
- [x] `request_exchange` refuses a size or colour that's sold out ("XL/White is sold out;
      left: M, XXL")
- [x] The owner's new-order alert names the variant that's running low ("Cotton Panjabi
      L/White: 1 left")

**The owner** (`api.py`, the CSV import, `../heydesk-frontend`)
- [x] `GET/PUT /api/products` carry `variants`
- [x] The CSV `stock` column also takes a count per size or colour: `"M: 5; L: 0; XL: 3"`, or
      `"L White: 2; L Navy: 0"`. A plain number stays the product's own stock
- [x] The Products page: when a product has sizes or colours, its edit form can count by size
      and colour instead of the one stock box: a list of counts, each a size (or any), a colour
      (or any) and a number. (A grid of boxes couldn't show "M in any colour" and would turn it
      into one count per colour.) The row shows what's sold out and what's low. The on/off switch
      stays for products without variants; with variants, the counts say it
- [x] `docs/api.md` in the frontend: `Variant` and the CSV format

**Docs and tests**
- [x] design.md (the products table, `update_cart`, the CSV import; the "not now" row for stock
      per size goes), decisions.md D56, prompt-rules.md (the product line), the seed (the
      Cotton Panjabi counted per size and colour)
- [x] `test_shop.py`: L/White at 0 is refused while M/White is sold; the last 2 of a variant are
      "only 2 left"; an order takes from its variant and a cancel puts it back; a size-only
      variant counts every colour; an unlisted size is not counted; the CSV formats; an
      exchange to a sold-out size is refused

**Done when:** `scripts/check.sh` and the frontend's lint are green; on the Products page a
panjabi's L/White is set to 0 and saved; and live on Maira with the seed shop (costs quota):
"L white panjabi ache?" says L/White is sold out and offers what's left; ordering 3 of a
variant with 2 left is refused with "only 2 left"; an order of M/White takes 1 from M/White
only.

Checked 13 Oct: the Products page saved L/White 0 and M (any colour) 4, kept them when another
product was switched; live on Maira with the seed shop, "L navy cotton panjabi ache?" → sold
out, L/White offered; 3 of XL/White → "only 2 left"; O-102 of M/White took M from 4 to 3 only.

## 13 Oct: photos in the shop (D57)

Facebook-page buyers send a screenshot and ask "eta ache?"; today the agent can only ask for
the name. The owner adds photos to products, a visitor sends a photo and the agent finds the
closest products, and the agent can show a product's photos. Web chat and the shop only.
Tested 13 Oct with `gemini-embedding-2` on 14 real products: the right one first 42/42.

**Storing and embedding a photo** (`services/photos.py`, the embedder, a Qdrant store)
- [x] `uv add pillow`. `PhotoService.save(ws, data)`: Pillow opens it (a 422 if it can't), makes
      a JPEG of at most 1024 px, writes `media/<workspace_id>/<id>.jpg` (`MEDIA_DIR`) → `Photo(id,
      url)`. `/media` is served as static files
- [x] `Embedder.embed_image(jpeg)`: the Gemini adapter sends it as `inline_data`, one image per
      request; the fake one hashes the bytes (the same photo matches itself)
- [x] `PhotoIndex` port and its Qdrant adapter: a `heydesk_photos` collection (768, cosine,
      dense only), `add(ws, photo_id, vector)`, `nearest(ws, vector, limit)` filtered by
      `workspace_id`. Built in `wiring.py` with the knowledge store
- [x] `PHOTO_MIN_SIMILARITY = 0.75` in settings (0 with the fake embedder, like the knowledge floor)

**The owner adds photos** (the shop's `api.py`, `values.py`, `models.py`, a migration)
- [x] `POST /api/products/photos` (multipart): save, embed, index → `{id, url}`. Embedding
      failed: a 503 and nothing kept
- [x] `Product.photos: list[str]` (at most 3 URLs), `products.photos` JSON; `GET/PUT
      /api/products` carry it. A URL that isn't one of the business's product photos is a 422;
      a CSV import keeps each product's photos by its code
- [x] `reindex-knowledge` re-embeds every product photo from its file

**A visitor sends a photo** (`api/chat.py`, `ConversationService`, the shop desk)
- [x] `ChatIn.photo: str | None` (base64, at most 2 MB decoded); `AgentOut.photos` from
      `Desk.takes_photos` (the shop only); a photo to another desk is a 422
- [x] `start_turn` saves it, embeds it and asks the index for the 10 nearest; the shop's
      `photo_line(store, matches)` keeps this business's products at or above the floor, one
      per product, best first, at most 3, and writes the line (design §7, Photos). The stored
      visitor message is that line, a new line, then their text, and has `photo` (the URL).
      Gemini or Qdrant down: the "couldn't be checked" line, never an error
- [x] The model never gets `photo` or `photos`: the turn drops them (`HIDDEN` in
      `runtime/loop.py`, with `by`)

**The agent shows photos** (the shop's `tools.py`, the runtime, SSE)
- [x] `show_photos(products)`: up to 3 codes → each one's name, price (as `update_cart` says
      it) and first photo's URL; a product with no photo is named in the result. Off the web
      chat: "photos can't be shown here; describe it"
- [x] `ToolResult.photos` → a `Photos(items)` event → SSE `photos {items}`; the tool's stored
      message has `photos` too

**The prompt** (the shop's `ROLE`, prompt-rules.md)
- [x] Replace "You can't send photos" and "ask for its name" with: a `[Photo. …]` line lists the
      closest products, best first. Show the first with show_photos and ask if it's the one; never
      say you have it before they say yes; if not, offer the others as similar. Nothing looks like
      it, or it couldn't be checked: say so and ask what it is. They ask for a photo:
      show_photos

**The frontend** (`../heydesk-frontend`)
- [x] Chat (`AgentChat.vue`, `api/chat.js`): when the agent says `photos`, a photo button next
      to the box; the picked file is shrunk in the browser (canvas, JPEG, 1024 px) and sent as
      `photo` with the message (the text may be empty); the visitor's bubble shows it
- [x] Chat: the `photos` event shows a row of product cards (photo, name, price) in the agent's
      reply
- [x] Products page: each product shows its photos; add (up to 3) and remove; saved with the
      product
- [x] Conversations page: a message's `photo` and `photos` show as pictures
- [x] `docs/api.md`: `photo`, `photos`, the upload route, the `photos` event

**Docs and tests**
- [x] design.md, decisions.md D57, prompt-rules.md (the shop's row). The seed stays without
      photos: `photos/` isn't in git, and a seed can't embed without a Gemini key
- [x] `test_shop.py` with the fake embedder: a product's own photo sent back is the first match;
      no photos indexed → "nothing looks like it"; the embedder failing → "couldn't be checked"
      and the turn still runs; another business's photo is never matched; `show_photos` sends
      the event; a photo to the restaurant is a 422

**Done when:** `scripts/check.sh` and the frontend's lint are green; on the Products page, the 14
photos in `photos/` are added to products and saved; and live on Maira with real Gemini (costs
quota): a screenshot of the Navy Shirt's photo in the chat → the agent shows the Navy Shirt and
asks if it's the one; "yes" then an order works as before; a photo of a landscape → it says
nothing looks like it and asks; "chobi dekhan" for a product → its photo appears.

Checked 13 Oct with the fake model and real Gemini: on the Products page a photo was added to
the Cotton Panjabi with Add photo and saved; four more products got your photos through the API.
In the chat, a phone screenshot of the Silk Panjabi's photo (the navy shirt) → "Closest products
in the shop: Silk Panjabi" alone; a drawn landscape with no words → "Nothing in the shop looks like
it"; the owner's Conversations page shows the visitor's photo. Live on Maira (13 Oct), with the
same photos: the screenshot + "eta ache?" → show_photos, the Silk Panjabi's card, "ki apni
khujchen?" without saying the shop has it; "ha eta. L size maroon nibo" → update_cart, L/Maroon
৳3,200; "cotton panjabi er chobi dekhan" → its card; the landscape alone → "Ei chobir moto kono
product amader shop-e nei. Eta ki jinis?". Seen on the way, not D57: Maira invented a
free-delivery nudge ("aro ৳?") that update_cart didn't give (the subtotal was already over it).

## 14 Oct: a linked Google Sheet (D58)

Most Bangladeshi Facebook shops and chambers keep their list on paper or in Excel; the khata
and POS apps and local store builders have no public API (research, 14 Oct). A Google Sheet is
the cheapest step up: the owner links it once, and the list (products and stock, the menu, the
doctors and who's in today) follows it every 5 minutes.

**Reading the sheet** (`ports/sheets.py`, `adapters/sheets.py`, `services/sheets.py`)
- [x] `csv_url`: a sheet's link → its tab's CSV export; only `docs.google.com`. `fetch_csv`
      (httpx): a private sheet gets Google's sign-in page, not CSV → "share it by link"
- [x] `sheet_links` (one per agent: url, mapping, seen, checked, synced_at, problem) and a
      migration. `SheetService`: suggest (as D53), link (as an import: problems per row,
      `dry_run`), get, sync, unlink, and `run`, a loop the lifespan starts
      (`SHEET_SYNC_MINUTES`, 5)
- [x] The same text as last time does nothing. A row with a problem, or a sheet that can't be
      read, keeps the old list; `problem` says why, and the owner hears once on Telegram
      (`TeamService.notify`)

**The list** (`desks/base.py`, each desk's store)
- [x] `Desk.columns` and `Desk.read_list`; `Store.take_sheet`: the menu and doctors are
      replaced; the shop merges (`merge`, `take_off`, `HOLDS`): a count comes from the sheet
      only when its cell changed, less the orders still `unpaid`, `new` or `confirmed`
- [x] While linked: `PUT /api/menu`, `PUT /api/doctors` and every import are 409
      `sheet_linked`; `PUT /api/products` takes only photos

**The owner** (`../heydesk-frontend`)
- [x] The list page: a "Google Sheet" button opens the import panel in sheet mode (paste the
      link, check the columns, see the rows, "Keep these N in sync"); while linked, a bar
      says when it was read or what's wrong, with "Read now" and "Stop syncing", and the rows
      are locked (a shop can still change photos). English and Bangla; `docs/api.md`

**Docs and tests**
- [x] design.md (the new section, the table, routes, layout, §12), decisions.md D58
- [x] `tests/integration/test_sheets.py`: the link's CSV URL; a price change keeps our count,
      a recount takes off the order not shipped yet, and not once it's shipped; a bad row and
      an unshared sheet keep the old list, and a good read clears it; the 409s, a preview
      without linking, the owner alerted once; a linked shop still takes photos

**Done when:** `scripts/check.sh` and the frontend's lint are green; in the browser (fake
model), a real public Google Sheet is linked from the Products page, the list becomes its
rows, "Read now" reads it and "Stop syncing" unlocks the list.

Checked 14 Oct: Google's public sample sheet (30 rows) linked from the dev shop's Products page
with Name and Price matched by hand (the fake model matches by name only); the bar showed
"Last read", the rows were locked with photos still open, Read now answered 200, Stop syncing
brought back Add and Import. The seed products were put back after.

## 14 Oct: one row per size and colour (D59)

The D58 live check on a real shop's sheet had a row per size and colour, so a T-shirt became
two products with the same name.

- [x] `grouped` in the shop's `products_from_csv`: rows with the same name and price are one
      product, the sizes and colours of all its rows, each row's count a variant (D56), the
      first row's code. Another price stays its own product. The import and a sheet get it
- [x] When every row has a count, a size and colour no row has is 0, so it's never offered
- [x] design.md (the import section), decisions.md D59; `test_shop.py`: four rows → a T-shirt
      with two variants and two at 0, another price apart, a single row unchanged

**Done when:** `scripts/check.sh` is green, and live on Maira with the linked sheet the
T-shirt is one product and "L size black ache?" isn't offered as available.

Checked 14 Oct: the shop's sheet re-linked, 100 rows → 99 products, the Classic Cotton T-Shirt
one product (M/Black 100, L/White 80); before the 0s, Maira said L/Black was available; after,
3 of 3 said sold out and offered L/White or M/Black.

## 15 Oct: photos on Messenger (D62)

A Facebook buyer's screenshot is matched like a web chat photo, and the agent shows products
on Messenger as a carousel. Shop only; Telegram after.

- [x] `MessengerApi.download(url)` and `send_cards(token, psid, cards)`; `MessengerGraph`
      (the URL never in an error), `FakeMessenger` records both
- [x] `ChannelService._answer`: a message's first `image` attachment (no `sticker_id`) on a
      desk that takes photos → `conversations.reply(..., photo=bytes)`; anything else not text
      still gets "I can only read text"
- [x] `ConversationService.reply` takes `photo` and collects `Photos` events into
      `Reply.photos`; `ChannelService` sends them with `send_cards` (absolute URLs from
      `public_url`) before the text, and a failed send is logged
- [x] `show_photos` works on `messenger` as on `web`
- [x] Tests (`test_messenger.py`): a photo → the turn gets the `[Photo. …]` line; `show_photos`
      on Messenger → one carousel with the photo's full URL, then the text; a sticker or a
      clinic's photo → "I can only read text"

**Done when:** `scripts/check.sh` is green, and on a real Page a screenshot of a product gets
"is it this one?" with its card, and "show me the shirt" sends its card.

## 15 Oct: photos on Telegram (D63)

The same as Messenger's photos, with Telegram's calls; the bot uploads the photos, so no
public URL is needed for them.

- [x] `TelegramApi.download(token, file_id)` (getFile, then the file) and
      `send_photo(token, chat_id, jpeg, caption)` (an upload); `FakeTelegram` records both
- [x] `ChannelService`: a `photo` message to the shop → its largest size downloaded, the caption
      as the words; `show_photos`'s products sent as photos captioned "name · price", before
      the text; `PhotoService.read`
- [x] `show_photos` works on every channel but voice
- [x] Tests (`test_telegram.py`): a photo with "eta ache?" → the `[Photo. …]` line and the
      words, the kurti's photo sent with its caption, then the text; a photo to the restaurant
      → "I can only read text"

**Done when:** `scripts/check.sh` is green, and with a real bot a screenshot of a product gets
"is it this one?" with its photo.

## 15 Oct: the restaurant's bookings, and after the order (D64–D66)

A restaurant can take table bookings, and its customers can track, change or cancel an order,
and hear in their chat when the owner moves it along. Orders for a later day wait (they change
D40).

- [x] `bookings` table (`T-101`; new → confirmed → seated → done, or cancelled or no_show);
      config `takes_bookings`, `seats`, `table_minutes`, `max_party`; `book_table` (the party
      size, `day_and_time`, a shared seat count, up to 3 free times nearby) and
      `cancel_booking` (code and name); `GET/PATCH /api/bookings`. With bookings off, no tools
      and no prompt section (`Desk.own_tools(config)`) (D64)
- [x] `track_order`, `change_order` (time, address, note; while new), `cancel_order` (while
      unpaid or new); the order from this chat needs no code; the code is `O-101` now, not
      `#101`; `Store.here()` and `TooLate` shared with the shop (D65)
- [x] Delivery `ready → out_for_delivery → delivered`, pickup `ready → collected`; the owner's
      ready / on its way / confirmed / cancelled is told in the customer's chat
      (`news()`, `TeamService.tell_visitor`, `Services.team`) (D66)
- [x] Tests (`test_restaurant.py`): a booking saved only after a yes; a full slot names free
      times; the seats counted when each table sits down; outside the hours and too many
      people refused; cancel by code and name; bookings off; another workspace sees none;
      track, change and cancel from this chat; marking ready adds the team's line
- [ ] `../heydesk-frontend`: a bookings screen, the booking fields, the delivery statuses
- [x] The dev chat page shows the team's lines too (it asks `/team` every 4 seconds)
- [x] **Done when:** in the dev chat (`DEV_AGENT_KIND=restaurant`), "table for 4 tomorrow at
      8 pm" is read back and saved as T-101 after a yes; a full slot offers another time; an
      order marked ready in the API shows "your order is ready" in the chat (15 Oct, live on
      Maira + Jev: T-101 for Fri 16 Oct 8 pm with a birthday note; a full 8 pm offered 6:00,
      6:30 and 9:30 pm; O-103 marked ready showed "Your order O-103 is ready to pick up.")

## M1 — The agent takes an order

**Day 4 — Real model**
- `GroqLLM` adapter: streaming, tool calls joined, `reasoning_effort` low; `GigalogyLLM`, the default
- The "right now" section of the prompt: channel style, the date and time, the next 14 days
- Tests: Groq chunk mapping with fake chunks; the prompt with the fixed clock

**Done when:** a real chat about the menu works. [x] (on Groq; Gigalogy waits for working keys, Q1)

**Day 5 — Tools: `update_order`**
- The tool contract and `run_tool` in `runtime/tool.py` (validates, timeout, never raises)
- The tool loop in `run_turn` (at most 6 model calls); conversation state saved with the turn
- `update_order`: the whole order in, the total computed in Python
- Items in their own table, one shape for every preset; listed in the prompt up to 60, else `search_items` (D17)
- Gigalogy tool calls: the tools in the query, a JSON reply read into a `ToolCall` (D16)
- Tests: `run_tool` failures, the order total, a tool call in the loop, Gigalogy's JSON calls, item search

**Done when:** "two biryanis and a borhani" → ৳1,030 in the reply. [x] (Groq: 4 of 5 runs said the total; Gigalogy: 3 of 5, the others asked "which biryani?")

**Day 6 — Tasks and the state note**
- Detail types (`text`, `person_name`, `phone`, `time_today`, `cart`) and `next_step`
- `set_details`; the state note at the end of the system prompt, rebuilt before every model call; tasks in the prompt
- Tests: each detail type, `next_step`, the state note

**Done when:** "for 7:30, I'm Kayes" → the next reply asks only for the phone. [x] (Groq 5 of 5, Gigalogy 3 of 3)

**Day 7 — The commit check: `place_order`**
- `JevDecider` (from Frontdesk's); `Confirmed[T]`; `confirm()` fails closed
- `orders` table and `OrderService`; `place_order` reads the order from state
- Tests: no yes → no row; yes → one row

**Done when:** a full order with a read-back and "yes" saves one row; "hmm, maybe" saves nothing. [x] (Groq and Gigalogy: "yes" saved order 101; after "maybe" the models asked again, and Jev alone refused "maybe", "yes but…" and a yes with no read-back)

**Day 8 — Status line and hand-off**
- A `status` event with the tool's label while it runs
- `handoff_to_human` tool; conversation marked `needs_human`; provider failure → polite hand-off
- Tests: progress before a tool; the hand-off

**Done when:** "Checking…" shows while a tool runs; "can I talk to a person?" shows the hand-off card. [x] (Groq: "Updating your order…", then the card; Gigalogy, 2 Oct: `handoff_to_human`, then the card)

**Day 9 — Tests and catch up**
- A test for each branch of `run_turn`
- One whole scripted order with `FakeLLM` + `FakeDecider`
- Read through the code and tidy it

**Done when:** the scripted order passes offline and `scripts/check.sh` is green. [x]

## M2 — Voice

**Day 10 — Voice spike**
- Try Pipecat (SmallWebRTC) first, LiveKit Agents if it fights you; STT + TTS (e.g. Deepgram + Cartesia)
- An echo call in the browser; measure first-sound time
- Log the choice in decisions.md

**Done when:** you talk, it answers out loud, and the decision is written down. [x] (2 Oct: Kayes heard the echo in Chrome; first sound 2.1s after the last word, D18)

**Day 11 — Order by voice**
- Speak sentence by sentence: Pipecat's TTS does it (D19)
- `voice/call.py`: final transcript → `start_turn(channel="voice")` → TTS
- A **Call** button on the dev page (from day 10)

**Done when:** one order placed by voice. [x] (2 Oct: Kayes placed an order by voice in Chrome)

**Day 12 — Spoken forms**
- Money, time, phone and order number in words (`runtime/speech.py`)
- `speakable()` turns each sentence into words before the TTS; the model sees display forms (D20)

**Done when:** the total and the read-back are heard as words. [x] (2 Oct: Kayes heard them in Chrome)

**Day 13 — Fillers**
- The voice channel says a tool's `filler` when it starts (not while already speaking)
- The platform rule: the model never says "let me check" itself

**Done when:** you hear the filler while the order is saved. [ ]

**Day 14 — Barge-in**
- Talking over the agent stops the audio and sets the turn's interrupt flag
- The old turn finishes and saves before the next starts; ignore "mm-hmm"

**Done when:** interrupting it mid-list stops it, and the next reply follows what you said. [ ]

**Days 15–16 — Latency and polish**
- Log timing marks; measure last word → first sound over 10 calls
- If slow: check effort, the thinking line, a smaller model for voice

**Done when:** P50/P95 are in the README and a voice order feels smooth. [ ]

## M3 — Presets and the API

**Day 17 — Receptionist preset**
- `presets/receptionist/` + seed; the `message` task
- `requests` table; `submit_request` with the commit check

**Done when:** with the receptionist in `.env`, a message is saved only after a yes. [ ]

**Day 18 — Presets and agents API**
- `GET /api/presets`; create, read and edit agents; per-field errors
- The dev page accepts `?key=` for any agent

**Done when:** in Swagger, change a price, and the agent quotes the new price. [ ]

**Day 19 — Orders, messages, conversations API**
- List orders and messages; a conversation with its messages; change an order's status

**Done when:** an order placed on the page shows up in the API with its conversation. [x] (2 Oct: integration test, chat API → orders and conversation API)

**Day 20 — Login**
- `users`, signup and login (pwdlib + pyjwt); `current_workspace` from the token
- `heydesk create-user` for the dev workspace

**Done when:** a second account can't see the first account's agent (404). [x] (2 Oct: integration test)

**Days 21–23 — Tidy up**
- Isolation test per resource; one error shape everywhere; `MAX_TURNS`
- README: how to run, switch presets, use the API; catch up

**Done when:** `scripts/check.sh` is green and a fresh clone runs from the README. [x] (2 Oct: fresh clone, fake model: page, chat and 149 tests)

## Demo (days 24–27) and buffer (28–30)

- Demo data in the seeds; a reset script; a five-minute demo script (web order, voice order, interruption, hand-off, edit the agent) — built 2 Oct: `examples/agents/*.yaml`, `scripts/reset.sh`, [docs/demo.md](docs/demo.md)
- Rehearse twice; record a backup video. Deploying is optional: browsers allow the microphone on `localhost`

**Done when:** two clean rehearsals in a row and a backup video. [ ]

**If you fall behind, cut in this order:** login (day 20) → the receptionist
preset (day 17) → barge-in (day 14). Never cut: the commit check, voice, and the trace.

## Open questions

| # | Question | Needed by |
|---|---|---|
| Q1 | ~~Groq API key~~ and ~~Gigalogy keys~~ in `.env`: both work (Gigalogy's 401 on 1 Oct was on their side and went away) | Day 4 |
| Q2 | ~~TypeSafe (Jev) API key~~ in `.env` (Frontdesk's), `DECIDER=jev` | Day 7 |
| Q3 | ~~Voice framework~~: Pipecat with SmallWebRTC (D18) | Day 10 |
| Q4 | ~~Speech-to-text~~: Gigalogy Chorus (D18). Text-to-speech: Piper (free, local) until the owner adds theirs | Day 10 |

## 8 Oct: the team on Telegram (D43)

The owner gets an alert for each order and hand-off, and answers visitors by replying to it.

- [x] `ToolResult.alert` → `TurnEnded.alerts`; `place_order`'s alert; a hand-off alert with the last lines said
- [x] `channels.owner_chat_id` (migration); `owner_link` on `GET /api/agent/telegram`; `/start owner_<code>`
- [x] `TeamService`: a reply with a `Ref:` reaches the visitor and makes the chat `human`; the visitor's messages are forwarded; `/done` gives it back
- [x] `GET /api/chat/{key}/{id}/team` for the web page; tests in `tests/integration/test_team.py`
- [ ] `../heydesk-frontend`: the owner's alerts link next to the bot; the chat page shows the team's replies
- [ ] **Done when:** with a real bot, an order alert arrives, and the owner's reply reaches a web and a Telegram visitor

## 7 Oct: Telegram (D42)

A business connects its own Telegram bot and visitors talk to the agent there. Alerts to the
owner come later.

- [x] `TelegramApi` port, `TelegramBot` adapter (httpx), `FakeTelegram`; `channels` table, `conversations.contact`
- [x] `PUT/GET/DELETE /api/agent/telegram`; the webhook `POST /api/telegram/{id}`, checked by its secret
- [x] `ConversationService.reply` (a whole reply, for any messaging app), `/start`, a quiet conversation starts afresh
- [ ] **Done when:** with a real bot and a tunnel, an order placed in Telegram is saved only after a yes

## 6 Oct: one folder per kind of business (D41)

The preset engine (YAML presets, a jobs library, a generic tasks engine, `set_details`, the state
note, generic `items` and `records`) made every business a combination of shared parts. Replaced
by desks; built and checked offline:

- [x] `desks/base.py` (`Desk`, `Store`), `desks/shared.py` (`take_message`, hand-off, documents search)
- [x] Clinic: `doctors` and `appointments` tables, `request_appointment(doctor, time, name, phone)`
- [x] Restaurant: `menu_items` and `orders` tables, `place_order(pickup_time, name, phone)`, its order note
- [x] Receptionist: its own fields (`about`, `services`), shared tools only
- [x] Routes: `/api/desks`, `/api/menu`, `/api/orders`, `/api/doctors`, `/api/appointments`, `/api/messages`
- [x] One fresh migration; seeds rewritten; `DEV_AGENT_KIND` replaces `DEV_AGENT_PRESET`
- [x] Cut: `search_items`, item attributes, the CSV import, admin-only `instructions`
- [x] **Done when:** each desk, live on Maira, takes its booking or order only after a yes (13 Oct: clinic A-102, after a "not sure" saved nothing; restaurant order 105; receptionist M-102; shop O-101 and O-102)
- [x] `../heydesk-frontend` uses the new routes (13 Oct: it did already; it now also edits D54's delivery `places`, `size_guide` and a product's `was_price`, and keeps them on save)

