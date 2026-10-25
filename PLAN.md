# HeyDesk — the plan

What's built, what's next, and how to know each step is done. How it works is in
[docs/design.md](docs/design.md); why, in [docs/decisions.md](docs/decisions.md).

> **Now:** the product is built through D73 (15 Oct): four desks, web chat, voice, Telegram,
> Messenger, payments, the owner's API and the admin panel's phases 1–2. **Cleanup before the
> presentation:** days C1 (tidy the repo and docs) and C2 (security, D74) done 15 Oct; C3 (Docker and
> CI) done 16 Oct. Live checks: Telegram and Messenger passed 17 Oct. Day W1, a linked WooCommerce
> store (D75), done 17 Oct. Day W2 (long lists, returning buyers, misheard names; D80, D81) done 18 Oct. Day W3, the website
> chat's settings (D82), done 18 Oct. W4, the bubble on any website, done 18 Oct. W5, the owner's page, done 18 Oct. Next: day V1, a Bangla voice (D84); then the voice check and rehearsing the demo. Day V2, the shop's confirmation call to a `/phone` page (D88), built 19 Oct. Days V3 and V4, the calls as an optional feature with a queue and their screens (D89), built 19 Oct; V4's live check (answer on a phone, say yes) is next. Day K1, the agent books the courier (D90), built 21 Oct, then removed (D95): the owner books the courier by hand. Day S1, the shop agent like a BD f-shop (D92), built 21 Oct; its live checks (the Jev yes words, a Banglish order) are next. Day A1, email codes for signup and a forgotten password (D93; no login until verified, D94), built 21 Oct; A2, its screens, done 21 Oct; A1's Brevo check passed 21 Oct. Days T1 and T2, moderators and their screens (D97), done 21 Oct. Day S2, delivery areas by district (D98), built 21 Oct; its live chat check is next. Day M1, buttons on Messenger and Telegram (D99), built 22 Oct; its live check on a real Page and bot is next. Days P1 and P2, photos in Cloudflare R2 (D101) and from the list, shown when searching (D102), built 22 Oct; their live checks are next. Day H1, WhatsApp as a channel (D103), built 22 Oct; its live check on a real number is next. Day B1, plans and billing (D106), built 22 Oct; its billing page and live payment are next. Day V5, voice only for the receptionist and the clinic (D108; the shop's confirmation calls off), done 23 Oct. Day K1, bKash beside SSLCommerz for a business's payments and our plans (D109), built 23 Oct; its live sandbox check is next. Day U1, cards in the chat (summary, receipt, tracking, doctors) and the website chat's buttons (D110), built 23 Oct; seeing each card on the dev chat page is next. Day U2, the cards on Messenger, Telegram and WhatsApp (D111), built 23 Oct; its live check on a real bot, Page and number is next. Day K2, a courier port with a fake courier that books, follows and delivers a confirmed shop order (D112), built 23 Oct; seeing an order go to delivered on the dev chat page is next. Day K3, Steadfast and Pathao back behind the port with each shop's login (D113), built 23 Oct; a live booking needs a merchant account. Returning visitors on every desk and channel, the web chat and calls included (D114), built 23 Oct; seeing it offer last time's details on the dev chat page is next. Column matching for imports without a model (D115), built 23 Oct. Plan changes priced (D116), built 24 Oct. **Next, from 25 Oct:** days R1–R20, what the "AI Practor" reel shows (docs/competitor-gaps.md G21–G47). Day R1, the agent feels human (D117), built 25 Oct; its live check on a real Page is next. Day F1, Connect with Facebook (D118), built 25 Oct; connecting a real Page with it is next, then R2.
> **Dates:** started Wed 30 Sep 2026 · presentation Sun 15 Nov.

**How to use this file:** do the next unticked day. Its bullets say what to build;
design.md has the details. Tick a box only when its **Done when** passes (tests green, or
you saw it work), then update **Now**. Keep each day to the normal path (design §12).

## Built

| When | What | Decisions |
|---|---|---|
| 30 Sep – 2 Oct | The core: settings, database, JSON logs, CI; the model and Decider ports with fakes; the tool loop; the commit check (`Confirmed[T]`, Jev); web chat over SSE; voice over WebRTC (Pipecat) with spoken numbers and barge-in; the API with login | D1–D21 |
| 4 Oct | One agent per business; owner and admin roles with an audit log; the knowledge base (Gemini embeddings, hybrid search in Qdrant) | D22–D36 |
| 6 Oct | Desks: one folder per kind of business (restaurant, clinic, receptionist), replacing presets; closed days are facts | D37–D41 |
| 7–8 Oct | Telegram as a channel; the owner's alerts and takeover on Telegram; the online shop desk | D42–D45 |
| 9–11 Oct | Paged lists; paying first through each business's own SSLCommerz store; CSV import for every desk; daily limits and rate limits on the public chat | D46–D52 |
| 13 Oct | AI column matching for imports; a smarter shop agent; track, change or exchange an order; stock per size and colour; finding a product by photo | D53–D57 |
| 14 Oct | A desk's list from a linked Google Sheet; one product from rows per size; Facebook Messenger | D58–D61 |
| 15 Oct | Photos on Messenger and Telegram; restaurant table bookings and order news in the chat; admins from the command line; changing your own password; the admin panel (open a business, its page, suspend with a reason, usage, cost and failed turns) | D62–D73 |

Each desk passed a live run on Maira with Jev (13 Oct): nothing was saved without a read-back and a yes.

## Next: cleanup before the presentation

### Day C1: tidy the repo and docs

- [x] `scripts/check.sh` green again; presentation material (`proposal_uml/`, PDFs) kept out of git
- [x] Stale bits: `.env.example` lists every setting; design.md's layout matches the folders;
      strict mypy covers `desks/` (the tools moved there)
- [x] decisions.md: a replaced or changed decision says so at the start of its row
- [x] PLAN.md: this short roadmap (the long day-by-day version is in git history)
- [x] README: what it is, the architecture, the stack and the security, with correct facts
- [x] docs/demo.md: the presentation's demo, on the frontend

**Done when:** `scripts/check.sh` is green, and the README alone says what HeyDesk is, how it's
built and how to run it. [x] (15 Oct)

### Day C2: security fixes

- [x] Signup checks the email before it makes the business, so a taken email leaves nothing behind
- [x] Signup is rate-limited per IP, like login (D52)
- [x] The secrets businesses give us (Telegram bot tokens, Messenger Page tokens, SSLCommerz
      store passwords) are encrypted in the database with a key from `.env`; production refuses
      an empty key; a migration encrypts the rows already there
- [x] design §12: changing a password doesn't end logins already made (a token lasts 7 days)

**Done when:** `scripts/check.sh` is green; tests show a taken email leaves no business, a fast
signup gets a 429, and a stored token isn't readable in the table, while Telegram still works. [x] (15 Oct, D74)

### Day C3: Docker and CI

- [x] A `Dockerfile` (uv, a non-root user, migrations on start) and `compose.yaml` with Qdrant
- [x] CI: uv's cache, older runs cancelled, `pip-audit` on the dependencies, the image built
- [x] README: running it with Docker

**Done when:** `docker compose up` serves `/health` and the dev chat with the fake model, and CI
is green on GitHub. [x] (16 Oct: compose locally; CI's check, audit and image green)

### Day W1: a linked WooCommerce store (D75)

- [x] `WooApi` port, `adapters/woo.py` and a fake; `products_from_woo` (fields, variations as
      variants, another price another product)
- [x] `woo_links`, and `woo_id`/`woo_sent` on `shop_orders` (one migration); `WooService`: link
      (checks the key can read and write), get, unlink, sync (push, then pull), run; started in
      the lifespan every `WOO_SYNC_MINUTES`
- [x] Routes `/api/agent/woo` and `/api/agent/woo/sync`; a sheet or a store, not both;
      `not_linked` covers the store
- [x] Tests (the fake): the fields, an order placed is created in Woo with our prices, a cancel
      moves it, a pull doesn't undo an order not written yet, a Read key is refused

**Done when:** `scripts/check.sh` is green, and against the local WooCommerce in Docker: linking
takes its products, an order placed in the dev chat shows in Woo's admin with its stock taken off,
and cancelling it in HeyDesk puts the stock back in Woo. [x] (17 Oct: on WooCommerce 9.4.3 behind a
local HTTPS proxy, a Read key was refused; O-101 for an L/Black, placed through the shop's tools with
a scripted model and yes, showed in Woo's admin as #22 at our ৳2,510 and took Woo's count from 1 to 0;
cancelled here, it was cancelled there and the count went back to 1. The dev chat with Maira and Jev
is still to try)

### Day W2: long lists, returning buyers, misheard names (D80, D81)

- [x] Over 60 products, the shop's prompt lists only the groups, and `find_product` looks them
      up by plain words (a linked WooCommerce store can have hundreds)
- [x] A buyer back on Telegram or Messenger gets `# Their last order`: offered once, used after a yes
- [x] Voice: a misheard name is taken as the closest listed one and checked with the caller

**Done when:** `scripts/check.sh` is green, and tests show a long list gives only the groups and
`find_product` finds the right line, and a second Telegram conversation has the last order's
details while another chat doesn't. [x] (18 Oct; how Maira uses them live is still to try)

### Day W3: the website chat's settings (D82)

- [x] `widget` in every agent's config: colour, side, nudge (the line that pops out of the
      bubble), up to 4 starters (tap-to-ask buttons) and a logo; each desk has its own starters
- [x] `GET /api/chat/{key}` returns the widget's settings, filled in like the greeting
- [x] `POST /api/agent/logo`: the business's logo, saved small and set on the widget
- [x] Tests: defaults per desk, a bad colour or a fifth starter is a 422, the logo is set and
      shown in the chat info

**Done when:** `scripts/check.sh` is green, and `GET /api/chat/{key}` for the dev agent shows its
desk's starters, then the owner's colour, starters and logo after they're saved. [x] (18 Oct:
the tests do it on the dev agent; a transparent PNG logo comes back as a 256 px JPEG on white)

### Day W4: the bubble on any website (`../heydesk-frontend`)

- [x] `public/widget.js` (plain JS, 3.5 KB compressed with its comments): the bubble in the business's colour and logo, on
      its side; it opens a window holding `/embed/:key` in an iframe; the nudge after 5 seconds,
      once a day; a red dot when the team replies while it's closed; the iframe loads when the
      page is idle, so the first open is instant
- [x] `/embed/:key`: the customer chat (`AgentChat`) in a window: a header with the logo, name
      and "Replies instantly", and a close button; a welcome with the greeting and the starters
- [x] The conversation is kept in the browser, so it stays when the visitor changes page or reloads

**Done when:** a plain HTML page on another port with the one line shows the bubble, a chat
there carries on after going to a second page, and a team reply shows the red dot. [x] (18 Oct,
with the fake model: `dev/widget-test` on :5500 showed the bubble and the nudge; a starter was
answered; on the second page the window was still open with the conversation; a team line added
while it was closed showed the red dot; full screen at phone size)

### Day W5: the owner's page and polish (`../heydesk-frontend`)

- [x] A "Website chat" page: colour, side, nudge, starters and logo, a live preview, and Copy code
      with short steps for WordPress, Wix and Shopify
- [x] Animations (the bubble turns into ✕, the window opens smoothly, less motion when the device
      asks), full screen on phones with the typing box above the keyboard, Esc closes

**Done when:** the line pasted into the local WooCommerce shop (`dev/woo-shop`) shows the bubble,
an order placed through it shows in Woo, and screenshots on a desktop and a phone look right. [x] (18 Oct: the owner's
"Chat bubble" section saved a red colour, the left side, a new line, a fourth question and a logo, and
the test site showed them all. The line in the WordPress shop's footer showed the shop's bubble, and it
looked right at 1280 px and at phone width, with the fake model. Then with Maira and Jev, through the
bubble on the shop: 1 Cotton Tupi to Mirpur 10 was read back at ৳360 (৳300 and ৳60 delivery), and the
yes saved O-103, which showed in Woo as #73 at ৳360 and took Woo's count from 11 to 10. Maira mixed
Bangla into English replies. Not tried: the typing box with a real phone's keyboard)

### Day V1: a Bangla voice (D84)

Why and the options compared: [docs/product-gaps.md](docs/product-gaps.md) §1. The voice is
`EMTIAZZ/bangladeshi-bangla-tts-vits` (VITS, Apache 2.0, a Bangladeshi accent): on 19 Oct it made
each test sentence in 0.6–1.3 s on the M2's CPU, about 7× faster than real time.

- [ ] `tts_server/`, its own uv project (torch and coqui-tts stay out of HeyDesk's): loads the
      model once, downloading it on first start; `POST /speak {text}` returns 16-bit PCM
- [ ] `voice/bangla_tts.py`: a Pipecat TTS service, shaped like `voice/chorus.py`, that sends each
      sentence to `BANGLA_TTS_URL`
- [ ] `VOICE_LANGUAGE` on the server (`bn`, the default, or `en`, D86): a `bn` call uses Deepgram
      with `language=bn` to hear and the Bangla voice to speak; `en` is Deepgram and Piper
- [ ] `speakable()` in Bangla: money, times, phone numbers and order codes in Bangla words
      ("৳360" → "তিনশো ষাট টাকা"); 1–99 is a lookup table, then শো, হাজার, লাখ
- [ ] A voice rule for `bn` agents: reply in Bangla script, and write English names in Bangla
      letters ("কটন টুপি"), since the voice drops Latin letters (docs/prompt-rules.md)
- [ ] design.md §10 and D84
- [ ] `tts_server/kaggle_tts.ipynb` (D85): the same `/speak` on a free Kaggle T4 with a more natural
      voice (BanglaBox or OmniVoice), behind a Cloudflare tunnel whose URL goes in `.env`
- [ ] `tts_server/kaggle_stt.ipynb` and `voice/whisper_stt.py` (D87): our Bangla–English Whisper on
      its own Kaggle T4; `STT_PROVIDER=whisper` sends each utterance to `WHISPER_STT_URL`

**Done when:** `scripts/check.sh` is green; tests give the Bangla spoken forms; and a browser
voice call to a `bn` agent hears a question in Bangla, answers in the Bangladeshi voice, and
reads an order's total back correctly.

### Day V2: the shop calls the buyer to confirm (D88)

We have no phone carrier yet (Telnyx, Twilio), so for the demo the "phone" is a web page on a
real phone: `/phone` waits for calls to one mobile number, rings, and on Answer opens the same
WebRTC call as the Call button. A carrier replaces the page later; the call itself stays.

- [x] `ShopConfig.confirm_call`: the call's first line, with `{code}` and `{total}`; Bangla in
      `shop-bn.yaml`
- [x] `services/calls.py` (`CallService`): the phone pages waiting per mobile; after a turn that
      placed an order (`new`), it rings that mobile's page `CONFIRM_CALL_AFTER_SECONDS` later
- [x] `GET /api/phone/rings?number=` (SSE: `ring {call, business}`), and `call=` on
      `POST /api/voice/offer`: the call starts in a new voice conversation that already has the
      first line, with `state.calling` the order; the agent says that line first
- [x] `confirm_order` (shop): after a yes to that line, `new` → `confirmed`, with an alert;
      `cancel_order`, `change_order` and `track_order` need no code on the call
- [x] The shop's note on the call (docs/prompt-rules.md)
- [x] `/phone`: the number once, then standby; a ringtone and vibration with Answer / Decline;
      the call with a timer and Hang up
- [x] design.md §10 and D88

**Done when:** `scripts/check.sh` is green; tests show an order rings the buyer's waiting phone
page, a yes on the call confirms it and nothing else does; and in the browser, an order in the
chat makes `/phone` ring, and answering it starts the call with the confirmation line.

### Day V3: confirmation calls, optional, with a queue (D89)

Turns D88's demo into a feature a shop turns on. The phone is still the `/phone` page: no phone
carrier is planned.

- [x] `calls` in the shop's config: `enabled` (off by default), `wait_minutes`, calling hours
      (`start`, `end`), `tries`, `retry_minutes`, and `opening` (D88's `confirm_call`)
- [x] A `calls` table, the queue: queued → ringing → talking → done, with an outcome (confirmed,
      cancelled, no answer, no decision, skipped, stopped), tries and the next try's time
- [x] An order saved `new` while calls are on is queued `wait_minutes` later; the queue worker
      (every `CALL_QUEUE_SECONDS`) calls the next one due: "Call now" first, then the earliest,
      inside the calling hours, while fewer than `MAX_LIVE_CALLS` are live, and only while the
      order is still `new`
- [x] Not answered in `RING_SECONDS`, declined, or no page open: tried again `retry_minutes`
      later, up to `tries`; then `no answer` and an alert for the owner
- [x] The call ends itself after the goodbye once the order is confirmed or cancelled; when it
      ends, its outcome comes from the order
- [x] Owner routes: `GET /api/calls` (waiting in queue order, or done; or one order's),
      `POST /api/calls` (call this order), `POST /api/calls/{id}/now`, `DELETE /api/calls/{id}`
- [x] design.md, D89, docs for the frontend (`../heydesk-frontend/docs/api.md`)

**Done when:** `scripts/check.sh` is green; tests show nothing is queued while calls are off,
the queue calls in order inside the hours, a missed call is tried again and then alerts the
owner, and only a yes on the call confirms the order. [x] (19 Oct, D89)

### Day V4: the calls in the owner's console (`../heydesk-frontend`)

- [x] Settings → Calls: the switch, the wait, the calling hours, the tries and the first line,
      with a link to the demo phone
- [x] A Calls page: Waiting in queue order (#1, #2…) with Call now and Cancel; Done grouped by
      day with the outcome, Call again and the transcript. It refreshes every 5 s
- [x] A shop order in the Inbox shows its call, and a new one has "Call to confirm"
- [x] English and Bangla; architecture.md

**Done when:** in the browser, with calls on, an order in the chat appears on the Calls page,
rings `/phone`, and after a yes on the call shows Confirmed there and on the order.

### Day K1: the agent books the courier (D90)

*Removed by D95 (21 Oct): the owner books the courier by hand. Day K2 is dropped.*

- [x] `ports/courier.py` (`CourierApi`, `CourierAccount`, `Parcel`, `Booked`, `Tracked`,
      `CourierError`); `adapters/courier/steadfast.py` and `pathao.py` (httpx; Pathao's token
      kept until it expires)
- [x] `courier_links`, and `parcel_id`/`courier_status` on `shop_orders` (one migration)
- [x] `CourierService`: link (checked with the courier), get, unlink; `book_soon` after the
      call's yes and the owner's confirm; `run` every `COURIER_SYNC_MINUTES` books what's left
      and reads each shipped parcel's status; the owner told on booking, a return and a problem
- [x] Routes `/api/agent/courier` and `/api/agent/courier/sync`; `track_order` gives the
      courier's words
- [x] Tests (a fake courier): only confirmed orders are booked, once; a refusal stays confirmed
      and is tried again; delivered and returned follow the courier; a wrong key is a 422
- [x] D91: a new order's mobile is looked up with Steadfast's fraud score once; the line is
      saved on the order and sent to the owner

**Done when:** `scripts/check.sh` is green, and against Pathao's sandbox: linking takes its test
store, an order confirmed in the dev chat is booked there with the right cash to collect, and
its status comes back.

### Day S1: the shop agent like a BD f-shop (D92, docs/shop-research.md)

- [x] One way to confirm an order: the advance per delivery area (`Zone.pay_first`, a migration
      from the shop-wide switch) confirms it with no call; `call_to_confirm` (on by default) for
      cash on delivery, or confirmed at once
- [x] The details in one message in a chat (one at a time on a call); the area from the
      address; no landmark asked for
- [x] `track_order` by the mobile alone, and this buyer's last order on Telegram or Messenger
- [x] `change_order` changes the items before the courier has it, with the stock swapped
- [x] The prompt: apni and ji, the read-back one detail per line, "the delivery man calls before
      he comes", bKash, Nagad or a card for the link; a plain "Assalamu alaikum" greeting
- [x] A live test of Banglish yes words with Jev (`tests/integration/test_live_yes.py`)
- [x] heydesk-web: each area's "Paid online first", "Call to confirm cash-on-delivery
      orders", a paid order marked confirmed, the Policies box asking for the door rules

**Done when:** `scripts/check.sh` is green; the live yes test passes (costs Jev quota); and in the
dev chat, in Banglish, one message gets the name, mobile and address asked for together, a
Sylhet order with an SSLCommerz sandbox store asks for the advance and, once paid, is confirmed
with no call.

### Day A1: verify the email, reset a forgotten password (D93, D94)

- [x] Signup emails a 6-digit code and gives no token; `POST /api/auth/verify` (email + code →
      token) and `/verify/resend`; login before it is a 403 `email_not_verified` (D94)
- [x] `POST /api/auth/forgot` (always 204) and `/api/auth/reset` (code + new password → token)
- [x] Stateless codes (an HMAC with a 15-minute window), rate limited; Brevo behind a `Mailer`
      port, the terminal without a key; a migration marks everyone before it verified
- [x] Live: with `BREVO_API_KEY` and `MAIL_FROM` set, a signup's code arrives in a real inbox
      and verifies (21 Oct: delivered and opened per Brevo; Brevo's "authorised IPs" must be off,
      or list the server's IP)

**Done when:** `scripts/check.sh` is green, and a real signup gets its code by email.

### Day A2: the code screens (`../heydesk-frontend`)

- [x] After signup, and after a login that answers `email_not_verified`, a "type the code we
      emailed you" screen, with "send it again"; the code logs them in
- [x] Login → "Forgot password?" → email → code and new password → logged in

**Done when:** in the browser, a new owner verifies and a forgotten password is reset.

### Day T1: moderators (D97)

- [x] A third role, `moderator`; `team_events` (one migration)
- [x] `/api/team/members`: list, add by email (an invite email with a reset code), send it
      again, remove; `/api/team/activity`
- [x] A moderator: the inbox, records and lists, and reading the settings, but a 403 on changing
      them, the Telegram bot and the team; removed → out on the next request
- [x] Each owner's and moderator's write that succeeds is in the activity, with their email
- [x] design.md, D97, the frontend's `docs/api.md`

**Done when:** `scripts/check.sh` is green; tests show an invited moderator joins with the code,
is refused the settings, is out at once when removed, and their changes stay in the activity. [x] (21 Oct)

### Day T2: the team screens (`../heydesk-frontend`)

- [x] Team (`/team`, "Your business"): the members (owner, moderators, invited), add by email,
      send again, remove (with a confirm); the activity, newest first, in words with a link
- [x] A moderator's console: no Settings, Knowledge, Channels or Team (their routes go Home),
      and no import, sync or unlink on the list
- [x] The reset screen says an invited moderator joins there

**Done when:** in the browser, an owner adds a moderator, who joins with the emailed code, sees
the inbox but not the settings, and is logged out once removed. [x] (21 Oct)

### Day S2: delivery areas by district (D98)

- [x] `desks/shop/districts.py`: the 64 districts, in English and Bangla, with the old spellings
- [x] `Zone.districts` (`places` removed, D100); an area with none is everywhere else (one at
      most); `match_zone`: the district's area, else everywhere else
- [x] `place_order` and `change_order` take `district` (the model reads it off the address)
      instead of `area`; the prompt lists districts, not every place
- [x] Both consoles: a Districts field per area; the Vue one sets
      its one pay-first switch on every area (it sent the shop-wide one, refused since D92)
- [x] The districts are picked from a searchable list of the 64 (English or Bangla), shown as
      chips with a ×: nothing to misspell (the Vue console; React keeps a plain dropdown)
- [ ] Live: in the dev chat, a Feni address gets Outside Dhaka's charge, "Tongi, Gazipur"
      Dhaka suburbs, and "Agrabad, Chittagong" Outside Dhaka, with no area asked for (costs
      model quota)

**Done when:** `scripts/check.sh` is green [x]; in the browser, an area's districts save
("ঢাকা" as Dhaka) and "Mirpur" is refused [x] (21 Oct); the live chat check above passes.

### Day M1: buttons on Messenger and Telegram (D99)

- [x] `ToolResult.choices`: the last tool's go on `TurnEnded` and `Reply`; `update_cart` offers
      the sizes or colours in stock when one is missing (`Product.in_stock`), `ask_for_yes`
      offers Yes and Change
- [x] Messenger quick replies and a Telegram inline keyboard under the reply; each
      `show_photos` card gets an Order button ("Order [code]")
- [x] A tap is the message's text: a Messenger postback's payload, a Telegram callback query's
      data (answered at once); the Page subscribes `messaging_postbacks`, the bot allows
      `callback_query`
- [x] The Telegram and Messenger channel lines say a tap arrives as its words
- [x] Tests: a tap is a turn, the buttons come from stock, a Yes with no read-back saves nothing
- [ ] Live: on a real Page and bot (connected again, so they get the taps; the Meta app's
      webhook has `messaging_postbacks`), a photo's Order button starts the order, the size
      buttons skip the sold-out one, and the Yes button places it after the read-back

**Done when:** `scripts/check.sh` is green [x] (22 Oct); the live check above passes.

### Day P1: photos in Cloudflare R2 (D101)

- [x] `MediaStore` port; `DiskMedia` (`MEDIA_DIR`, served at `/media`) and `R2Media` (signed S3
      requests over httpx); `build_media` picks R2 when `R2_BUCKET` is set
- [x] `PhotoService` saves, reads, deletes and re-indexes through it; `is_products` checks the
      address (no `../`); Messenger cards take an R2 URL as it is
- [x] design.md (Photos), D101, `.env.example`
- [x] Tests: Amazon's signing examples, the disk store, a photo address from another business
- [ ] Live: with a real bucket, an owner's photo upload shows on the Products page, a
      visitor's photo finds it, and a Telegram card shows it

**Done when:** `scripts/check.sh` is green; the live check above passes (needs an R2 bucket and
its token in `.env`).

### Day P2: photos from the list, shown when searching (D102)

- [x] A `photos` column in a CSV and a sheet (up to 3 links; a product's rows add theirs up);
      a WooCommerce product's `images`
- [x] `PhotoService.take`: a link's name is its SHA-256, so it's downloaded and indexed once;
      `ShopStore.with_photos` at an import, a sheet's sync and a Woo sync; no links keeps ours
- [x] `fetch_image`: public http(s) only, each redirect checked, 10 MB; localhost in development;
      Google Drive share links
- [x] `find_product` shows its first 3 with a photo; the prompt shows up to 3 that fit when
      asked (docs/prompt-rules.md)
- [x] design.md (Photos), D102
- [x] Tests: links taken once, one that fails tried again, ours kept, a visitor's photo finds
      one; Woo's images; `find_product`'s cards and none on a call; private addresses refused
- [ ] Live: a sheet with Google Drive links and the test Woo shop's images become photos, and
      "panjabi ache?" in the dev chat shows them

**Done when:** `scripts/check.sh` is green; the live check above passes.

### Day H1: WhatsApp as a channel (D103, G12)

- [x] `WhatsAppApi` port, `WhatsAppCloud` (Cloud API over httpx, the token in a header) and
      `FakeWhatsApp`; `PUT/GET/DELETE /api/agent/whatsapp {token, account_id, phone_id}`
- [x] `/api/whatsapp`: the verify check and signed posts, routed by `phone_number_id`;
      statuses skipped; the turn is `_answer`'s, marked read with "typing…"
- [x] Reply buttons (a list past 3), a tap's id as the text; a buyer's photo by its media id;
      `show_photos` as one message a product with an Order button; the owner's reply from Telegram
- [x] The WhatsApp channel line (docs/prompt-rules.md); design.md (WhatsApp), D103, `.env.example`
- [x] Tests: connect and refuse, a number belongs to one business, the signature, a voice note,
      a photo and its Order tap, disconnecting, the owner answering
- [ ] Live: in the Meta app, add WhatsApp and the webhook (`PUBLIC_URL/api/whatsapp`, field
      `messages`); connect a number; a message is answered, a photo gets its card, its Order
      button starts the order, the Yes button places it after the read-back

**Done when:** `scripts/check.sh` is green [x] (22 Oct); the live check above passes, then tick
G12 in docs/competitor-gaps.md.

### Day B1: plans and billing (D106, G5)

- [x] `PLANS` (trial, Starter ৳2,490, Growth ৳5,990, Pro ৳12,990), `workspaces.plan` and
      `paid_until`, `subscription_payments` (one migration; businesses already here get a 14-day
      trial, the dev workspace Pro)
- [x] `BillingService`: the billing page's numbers, checkout through our SSLCommerz store,
      settle (once, this amount), an admin's plan by hand; `admit` refuses once the plan ended
      or its replies ran out; the plan's moderators cap invites
- [x] Routes `/api/billing`, `/api/billing/checkout`, the return and the notice (owners only);
      `PUT /api/admin/businesses/{id}/plan`; the plan in the admin's business list
- [x] design.md (Plans and billing), D106, `.env.example`
- [x] Tests: the trial, its one moderator, the end with grace, the replies used up, a payment
      settled once and only for the right amount
- [ ] The billing page in `../heydesk-frontend`: the plan, replies used, the plans, Pay
- [ ] Live: with a sandbox store in `BILLING_STORE_*`, pay for Growth with a test card; the
      billing page shows Growth until a month from today

**Done when:** `scripts/check.sh` is green [x] (22 Oct); the billing page and the live check
above pass, then tick G5 in docs/competitor-gaps.md.

### Day B2: changing plans, priced (D116)

- [x] `billing.quote`: full price on the trial or after the end; the same plan adds the days;
      a bigger plan pays the difference for the days left and keeps the end day; a smaller plan
      waits for the end; never more moderators than the plan has
- [x] `settle` prices it again under a row lock; a plan changed meanwhile holds it as `review`
- [x] `subscription_payments.kind`, the `review` status, `workspaces.months` (one migration);
      `GET /api/billing/quote`; the payments in `/api/billing`
- [x] design.md (Plans and billing), D116
- [x] Tests: an upgrade's price and end day, a year of Starter then Pro, a smaller plan refused
      until the end, a team too big for the plan, a stale upgrade held for review
- [ ] The billing page in `../heydesk-frontend`: the quote before Pay, a smaller plan's
      "once it ends", the receipts

**Done when:** `scripts/check.sh` is green [x] (24 Oct); the billing page shows the quote.

### Day V5: voice only for the receptionist and the clinic (D108)

- [x] `Desk.takes_voice` (like `takes_photos`): True for the receptionist and the clinic
- [x] `/api/voice/offer` refuses an agent whose desk takes no voice; `/agent` info says `voice`
      so the chat page hides its call button
- [x] The shop's confirmation calls are off: no call is queued, "Call to confirm" is refused
      (the code stays, unused for now)
- [x] design.md, D108, docs/demo.md if it shows a shop or restaurant call
- [x] Tests: a clinic call starts, a shop call is refused, a shop order queues no call

**Done when:** `scripts/check.sh` is green [x] (23 Oct).

### Day K1: bKash, for a business's payments and for HeyDesk's plans (D109)

- [x] `BkashCheckout` (Tokenized Checkout over httpx: grant a token, create, execute after the
      callback) and `FakeBkash`, behind `PaymentGateway`; the account's keys: app key, app
      secret, username, password
- [x] `payment_accounts.provider` (`sslcommerz` or `bkash`), one of each per business, the
      secrets encrypted (one migration); connect and disconnect bKash on the owner's routes
- [x] `/pay/<id>` shows a button for each connected gateway; bKash's callback is checked with
      bKash (this payment, this amount, once), then the row moves on as with SSLCommerz
- [x] Billing: `BILLING_BKASH_*` settings; `/api/billing/checkout` takes `gateway`; the
      callback settles the plan the same way
- [x] design.md, D109, `.env.example`, docs/api.md in `../heydesk-frontend`
- [x] Tests (the fake): connect and refuse, a bKash payment moves an order once and only for
      its amount, a plan paid by bKash, a failed or cancelled callback changes nothing
- [ ] Live: bKash's sandbox, an order paid and a plan paid

**Done when:** `scripts/check.sh` is green [x] (23 Oct); the live check above passes.

### Day U1: cards in the chat, and the website chat's buttons (D110)

- [x] `ToolResult.card` and a `Card` event `{kind, data}`, the data built by Python (rule 1);
      the kinds: `summary` (the read-back: lines, delivery, total), `receipt` (number, total,
      status, a pay link when unpaid), `tracking` (the order's steps), `doctors`
- [x] The tools that give them: `ask_for_yes`'s read-back → summary; a saved order, booking
      or appointment → receipt; `track_order` → tracking; `find_doctor` → doctors
- [x] SSE sends `card` and `choices`; the dev chat page (`static/chat.js`) shows both
- [x] docs/prompt-rules.md if the model is told anything new (it isn't); design.md, D110
- [x] Tests: each tool's card, the numbers from Python, no card on a voice call

**Done when:** `scripts/check.sh` is green, and the dev chat shows each card.

### Day U2: the cards on Messenger, Telegram and WhatsApp

- [x] `Reply.card` through `ChannelService`; each adapter sends it in its own way: summary as
      formatted text with Yes/Change, receipt with a Pay URL button (Messenger button
      template, Telegram URL button, WhatsApp CTA URL), tracking as steps, doctors as a
      Messenger carousel, Telegram messages with Book, a WhatsApp list
- [x] Tests (the fakes): each card on each channel

**Done when:** `scripts/check.sh` is green; the live check on a real bot, Page and number.

### Day U3: the screens in `../heydesk-frontend`

- [x] The chat (`AgentChat.vue`, the bubble): the buttons, and a component for each card
- [x] The call button only when the agent says `voice`
- [x] Payments: connect bKash beside SSLCommerz; billing: Pay with bKash
- [x] en and bn strings; docs/api.md

**Done when:** `pnpm lint` and `pnpm build` pass [x] (23 Oct), and in the browser each card shows.

## Next: what the "AI Practor" reel shows (from 25 Oct)

The gaps are in [docs/competitor-gaps.md](docs/competitor-gaps.md) (G-numbers). Tick the gap there
too when its day passes. A day that changes the design adds its D-number then. Each screen goes in
`../heydesk-frontend` the same day, en and bn. Comments (R15, R16) need Meta App Review for
`pages_manage_engagement` and `pages_read_user_content`: **start that paperwork on day R1.**

### Day R1: the agent feels human (G21–G24)

- [x] Many bubbles, one turn: `ChannelService._answer` waits ~3 s after a chat's last message
      (in memory: each message sleeps, and the last one to arrive takes them all) and runs one
      turn on all of them (D117)
- [x] Messenger: `mark_seen` then `typing_on` while a turn runs (Telegram and WhatsApp have typing)
- [x] A reply to an earlier message: Messenger `reply_to.mid`, Telegram `reply_to_message`,
      WhatsApp `context.id` → `(replying to: "…")` before the text; a quoted photo is searched
- [x] A platform line: address the buyer the way the shop does (Bhaiya/Apu, Sir/Ma'am) and keep
      it once known (docs/prompt-rules.md)
- [x] Tests: three quick bubbles are one turn and one reply; a quoted message reaches the model
- [ ] Live: on a real Page, three quick messages get one reply after "seen" and typing

**Done when:** `scripts/check.sh` is green; on a real Page, three quick messages get one reply
after "seen" and typing.

### Day F1: Connect with Facebook (D118)

Copying a Page id and token out of Meta's dashboard is too hard for a shop owner. A button opens
Facebook's own login; the owner picks their Page and it's connected. While our Meta app is in
development mode this works for anyone with a role on the app (admin, developer, tester), enough
for the demo; for every business it needs Business Verification and App Review (started on R1).

- [x] `META_APP_ID` in settings; `GET /api/agent/messenger/login` → `{app_id, scope}`
- [x] `MessengerApi.pages(user_token)`: a long-lived user token (so the Page tokens don't expire),
      then the Pages it manages with their tokens
- [x] `POST /api/agent/messenger/pages {user_token}` → `[{id, name}]`, never a token;
      `POST /api/agent/messenger/facebook {user_token, page_id}` → connects that Page
- [x] Frontend: Facebook's JS SDK, "Connect with Facebook", one Page connects at once, several
      are a list to pick; the old id-and-token form stays behind a small link (en, bn)
- [x] Tests: a login lists its Pages without tokens; picking one connects it; a Page not in the
      login is refused

**Done when:** `scripts/check.sh` is green; on the owner's page, Connect with Facebook connects a
real Page and it answers a message.

### Day R2: product cards side by side (G25, G27, G16)

- [ ] A `products` card from `find_product`: up to 10, each a photo, name, price, *I want this*
      (the code as the tap's text) and *Details*
- [ ] Each channel its way: Messenger carousel, WhatsApp list, Telegram a photo each, web chat a row
- [ ] `videos` (links) on a product, from the list and the sheet; `show_photos` sends them
- [ ] `goes_with` (codes) on a product; `update_cart` returns it and the prompt offers it once
- [ ] Tests: the card's prices come from Python; *I want this* starts the order; a video link is sent

**Done when:** `scripts/check.sh` is green; on the dev chat page and a real Page, a search shows
the cards and a tap starts the order.

### Day R3: a product page and an order page (G26)

- [ ] `/p/<code>`: photos, price, sizes, delivery time, *Buy now* → a short form; server-rendered
- [ ] The form's submit is the buyer's yes: it saves through `Confirmed[T]` (a decision, D-next)
- [ ] The receipt card goes into the buyer's chat when the page was opened from it
- [ ] `/o/<id>`: the order's status, linked from every receipt (an unguessable id)
- [ ] Tests: an order from the page is saved once, with Python's total; a wrong id is a 404

**Done when:** `scripts/check.sh` is green; from Messenger, *Details* → *Buy now* → the receipt
lands back in the chat.

### Day R4: order steps, money in, team orders, export (G28, G30, G6)

- [ ] Statuses *on hold* and *packed*; on delivered or returned, `paid_in` and a `reason`
      (returned, damaged, refused, cancelled because…)
- [ ] The owner adds an order for a buyer who called: its own writing method, no yes (D-next)
- [ ] `GET /api/shop/orders.csv` (and restaurant orders, bookings, appointments) with the list's filter
- [ ] Screens: the new statuses, *money in*, a reason, *New order*, *Export*
- [ ] Tests: a team order is saved and alerted; a CSV has the filtered rows and no other business's

**Done when:** `scripts/check.sh` is green; in the browser, an order moves on hold → packed →
delivered with money in, and the CSV opens in a spreadsheet.

### Day R5: the buyer's record (G29, G17)

- [ ] A buyers list grouped by mobile: orders, returns, cancels, channels, conversations
- [ ] On each order: "3 orders, 1 returned" from ours, plus Steadfast's fraud check when linked
- [ ] Tests: the counts; another business's orders never counted

**Done when:** `scripts/check.sh` is green; in the browser, an order shows its buyer's record.

### Day R6: coupons (G31)

- [ ] A `coupons` table: code, percent or amount or free delivery, end date, max uses
- [ ] `update_cart` takes `coupon`; Python checks it and puts the discount on the bill (rule 1);
      a bad code is `ok=False` with a hint
- [ ] Screens: the coupons list and form
- [ ] Tests: each kind's total, an ended code, a used-up code, the read-back shows the discount

**Done when:** `scripts/check.sh` is green; in the dev chat, "code EID10" puts 10% on the bill.

### Day R7: delivery-day reminder and a review request (G32)

- [ ] The courier round: on "out for delivery", remind the buyer; a day after delivered, ask how
      it was (👍 / 👎 / write). Once each, and only inside each app's window
- [ ] Tests (the fake courier): one reminder, one review request, none outside the window

**Done when:** `scripts/check.sh` is green; on the dev chat page, the fake courier's delivery
brings both messages.

### Day R8: take over with a dot (G33)

- [ ] Messenger `message_echoes`: the Page's own messages (not ours) arrive and are added to the
      conversation as the team's (rule 5)
- [ ] "." from the owner stops the agent for that chat; "." again, or 30 min of quiet, hands it back
- [ ] The same from our inbox screen (a *Take over* switch) and on Telegram and WhatsApp
- [ ] Tests: while taken over, a buyer's message runs no turn; the owner's words are in the next
      turn's history

**Done when:** `scripts/check.sh` is green; on a real Page, "." in Business Suite silences the
agent, and "." gives it back.

### Day R9: the inbox: search, done, snooze, assign (G34)

- [ ] Search names, phones, order codes and message text (`ILIKE`; Postgres full text later)
- [ ] *Done* hides a chat until the buyer writes again; *Snooze until* brings it back with an alert
- [ ] *Assign* to a moderator, who gets the alert
- [ ] Screens: the search box, the three actions, *Mine* and *Done* filters
- [ ] Tests: a done chat comes back on a new message; a snoozed one at its time; search stays in
      one business

**Done when:** `scripts/check.sh` is green; in the browser, each action works on a real chat.

### Day R10: notes, labels, saved replies, the buyer beside the chat (G9, G35)

- [ ] `labels` on `conversations` with a filter; a `notes` table (insert only) the visitor never sees
- [ ] Saved replies in the agent's config, picked from the reply box
- [ ] The chat screen's side: the buyer's orders, bookings and notes (R5's record)
- [ ] Tests: a note never reaches the prompt or the visitor

**Done when:** `scripts/check.sh` is green; in the browser, a labelled chat with a note shows its
buyer's orders.

### Day R11: AI help for the moderator (G36, G14)

- [ ] On a chat: *Summary*, *Ask about this chat*, *Draft a reply*: the model with no tools
- [ ] Nothing is sent until the moderator sends it; each is a traced, billed turn
- [ ] Tests (FakeLLM): a draft is shown, not sent

**Done when:** `scripts/check.sh` is green; in the browser, a draft fills the reply box.

### Day R12: complaints become tickets; AI off for one buyer (G37, G10, G38)

- [ ] `flag_complaint(reason)`: hands off (D43) and opens a ticket: who has it, open or solved, age
- [ ] A tickets list; a ticket stays open until someone marks it solved
- [ ] *Turn AI off for them*: their messages are kept but run no turn and cost nothing
- [ ] Tests: a complaint opens one ticket and alerts; a buyer with AI off runs no turn

**Done when:** `scripts/check.sh` is green; in the dev chat, an angry message opens a ticket.

### Day R13: nudges and leads (G7, G39)

- [ ] A timed round: a read-back with no yes, 1–3 h later and inside the window, gets one nudge
- [ ] `take_lead(when, note)`: "call me Friday 5pm" → a lead at that time (Python reads the date)
- [ ] A leads list; the team taps the outcome (sold, not now, lost: why); the *why* counts
- [ ] Tests: one nudge, never two; the lead's time from FIXED_NOW

**Done when:** `scripts/check.sh` is green; in the dev chat, a callback request shows in leads.

### Day R14: an offer to everyone who wrote today (G40)

- [ ] The owner writes an offer with an end time; it goes to everyone who wrote in the last 24 h
      (inside Messenger's window), and who got it is saved
- [ ] `update_cart` applies it only to them and only until it ends (R6's discount path)
- [ ] Tests: a buyer who got it gets free delivery; one who didn't, or after the end, doesn't

**Done when:** `scripts/check.sh` is green; on the dev chat page, the offer reaches a recent chat
and its bill.

### Day R15: comment to inbox (G1, G43)

- [ ] Subscribe the Page's `feed`; a new comment gets one public reply and one private reply
      that opens the Messenger chat with the post's product
- [ ] The comment goes into that buyer's conversation (same Page-scoped id)
- [ ] Tests: one comment, one public and one private reply; a second comment from them adds no
      second private reply

**Done when:** `scripts/check.sh` is green and App Review passed; on a real post, "dam koto?"
gets both replies.

### Day R16: posts with many products, and one more nudge (G41, G42)

- [ ] A post → products map: the owner tags it, or its photos are matched (D57)
- [ ] Many products: the price range in the comment, R2's cards in the inbox
- [ ] No answer in ~2 h: one more comment reply, once
- [ ] Tests: the range from Python; one nudge only

**Done when:** `scripts/check.sh` is green; on a real post with three products, the inbox gets
three cards.

### Day R17: what it couldn't answer, and practice mode (G44, G46)

- [ ] Save a question when `search_knowledge` finds nothing or the turn hands off for "don't
      know"; a list grouped by likeness with a count, and *Teach it* (a knowledge entry)
- [ ] Practice mode: a new agent answers only the owner's listed test accounts until *Go live*
- [ ] Tests: a taught answer is found next time; practice mode ignores a stranger

**Done when:** `scripts/check.sh` is green; in the browser, a missed question is taught and then
answered.

### Day R18: change the agent in plain words (G45)

- [ ] The owner writes the change; the model rewrites the editable prompt section; a test chat
      shows the new version; *Save* or *Undo*; the last few versions kept (bends D15: D-next)
- [ ] Platform rules stay untouched (rule 3); docs/prompt-rules.md
- [ ] Tests (FakeLLM): nothing changes until *Save*; *Undo* brings back the last one

**Done when:** `scripts/check.sh` is green; in the browser, a change is tried, saved and undone.

### Day R19: the owner's dashboard (G4)

- [ ] `GET /api/stats?days=7`: conversations, orders or bookings and their total, chats that
      ended in an order, AI-handled vs handed off, where each order came from (chat, page, team),
      leads not bought
- [ ] One card row on the owner's home page; today and yesterday
- [ ] Tests: the sums from seeded rows; one business only

**Done when:** `scripts/check.sh` is green; in the browser, the cards match the lists.

### Day R20: service businesses: reminders and the advance's TrxID (G18, G47)

- [ ] A timed round: a reminder the day before a booking or appointment (Telegram; Messenger and
      WhatsApp only where Meta's rules allow, check first)
- [ ] The booking takes the bKash or Nagad TrxID the buyer typed; the owner ticks it checked
- [ ] Tests: one reminder per booking; the TrxID on the booking

**Done when:** `scripts/check.sh` is green; in the dev chat, a clinic booking keeps its TrxID and
gets its reminder.

## Still open

Live checks that need a real account, a device or a person:

- [x] Telegram: an order placed in a real bot is saved only after a yes (D42); the owner's reply
      to an alert reaches a web and a Telegram visitor (D43); a product's photo gets "is it this
      one?" (D63) (17 Oct: "na thak" saved nothing, a yes saved O-106 and O-107; a photo matched
      the Linen Panjabi; the owner's replies reached both visitors)
- [x] Messenger: a real Page answers, and a screenshot of a product gets its card (D60, D62).
      Needs a Meta app, its secret and verify token in `.env`, a Page token and a tunnel
      (17 Oct: the Page answered as the shop; a screenshot matched the Linen Panjabi, and its card
      showed above the text)
- [ ] Voice: the filler is heard while an order is saved; talking over it stops it; P50/P95 of
      the first sound in the README (`scripts/latency.py`)
- [ ] Demo: two clean rehearsals in a row, and a backup video

In `../heydesk-frontend`:

- [ ] A bookings screen, the booking fields and the delivery statuses (D64–D66)
- [ ] The owner's alerts link next to the Telegram bot; the chat page shows the team's replies (D43)

## Later

- **Admin panel, phase 3 (abuse and channel health):** blocked visitors with an unblock; the
  last error of each Telegram or Messenger channel and linked sheet, on the business's page.
- **Before real customers:** Postgres; "Forgot password?" by email; two-factor login and shorter
  sessions for admins.
- **More sources for a shop's list:** Shopify, after Google Sheets (D58) and WooCommerce (D75).

## History

Every day's bullets and checks, from day 1 to admin day B3, are in this file's git history
(`git show 642dc26:PLAN.md`), and each change is a commit naming its decision.
