# HeyDesk — the plan

What's built, what's next, and how to know each step is done. How it works is in
[docs/design.md](docs/design.md); why, in [docs/decisions.md](docs/decisions.md).

> **Now:** the product is built through D73 (15 Oct): four desks, web chat, voice, Telegram,
> Messenger, payments, the owner's API and the admin panel's phases 1–2. **Cleanup before the
> presentation:** days C1 (tidy the repo and docs) and C2 (security, D74) done 15 Oct; C3 (Docker and
> CI) done 16 Oct. Live checks: Telegram and Messenger passed 17 Oct. Day W1, a linked WooCommerce
> store (D75), done 17 Oct. Day W2 (long lists, returning buyers, misheard names; D80, D81) done 18 Oct. Day W3, the website
> chat's settings (D82), done 18 Oct. W4, the bubble on any website, done 18 Oct. W5, the owner's page, done 18 Oct. Next: day V1, a Bangla voice (D84); then the voice check and rehearsing the demo. Day V2, the shop's confirmation call to a `/phone` page (D88), built 19 Oct. Days V3 and V4, the calls as an optional feature with a queue and their screens (D89), built 19 Oct; V4's live check (answer on a phone, say yes) is next. Day K1, the agent books the courier (D90), built 21 Oct; its live check waits for Pathao's sandbox (down on 21 Oct). Day S1, the shop agent like a BD f-shop (D92), built 21 Oct; its live checks (the Jev yes words, a Banglish order) are next. Day A1, email codes for signup and a forgotten password (D93), built 21 Oct; A1's Brevo check and A2's screens are next.
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

### Day K2: the courier in the owner's console (`../heydesk-frontend`)

- [ ] Settings → Courier: pick Steadfast or Pathao, paste the keys, see a problem
- [ ] An order shows its parcel, the courier's words, the buyer's courier record (D91) and a
      link to track it

**Done when:** in the browser, the courier links, and a confirmed order shows its parcel.

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

### Day A1: verify the email, reset a forgotten password (D93)

- [x] Signup emails a 6-digit code; `POST /api/auth/verify` and `/verify/resend`; until then the
      agent's chat is `unavailable` and `missing` says so; `email_verified` in `/api/auth/me`
- [x] `POST /api/auth/forgot` (always 204) and `/api/auth/reset` (code + new password → token)
- [x] Stateless codes (an HMAC with a 15-minute window), rate limited; Brevo behind a `Mailer`
      port, the terminal without a key; a migration marks everyone before it verified
- [ ] Live: with `BREVO_API_KEY` and `MAIL_FROM` set, a signup's code arrives in a real inbox
      (not spam) and verifies

**Done when:** `scripts/check.sh` is green, and a real signup gets its code by email.

### Day A2: the code screens (`../heydesk-frontend`)

- [ ] After signup, a "type the code we emailed you" screen, with "send it again"; a banner
      while `email_verified` is false
- [ ] Login → "Forgot password?" → email → code and new password → logged in

**Done when:** in the browser, a new owner verifies and a forgotten password is reset.

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
