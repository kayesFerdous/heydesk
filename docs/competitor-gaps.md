# What competitors have that HeyDesk doesn't

Written 22 Oct 2026 from each competitor's own site (listed at the end). Researched, not built.
The other gap files cover other things: [product-gaps.md](product-gaps.md) covers voice, phone
checks, tokens and Meta review, and [shop-research.md](shop-research.md) covers how a shop takes
an order. This file covers only features that competitors sell and we don't.

**How to use this file (coding agents too):**

1. To build a gap, add it to [PLAN.md](../PLAN.md) as a day with its own *Done when*. A gap that
   changes the design also needs a line in [decisions.md](decisions.md), as usual.
2. When that day's *Done when* passes, **tick its box here** and add the date and D-number:
   `- [x] **G3 …** *(built 30 Oct, D101)*`.
3. If a gap is dropped or replaced, don't delete it. Move it to *Decided against* with one
   line saying why.

Sizes are rough: **S** is a day or less, **M** is two or three days, **L** is a week or more.
**Meta** means it needs Meta App Review or Business Verification first (product-gaps §6), so
start that paperwork early.

## The competitors

| Who | Price (BDT a month) | Channels | What they lead on |
|---|---|---|---|
| **Sellzy** (BD) | ৳0.16 a message, prepaid | Messenger, FB comments, Instagram, WhatsApp, WooCommerce | Comment-to-DM, courier booking, fraud checks, order export |
| **LazyChat** (BD) | ৳12,500 / 26,000 | Messenger, Instagram, WhatsApp, web | Comment automation, broadcasts, voice-note understanding, mobile app, complaints inbox |
| **ARA** (SysSolution, BD) | ৳3,500 – 55,000 + VAT | Voice (calls in and out), WhatsApp, Telegram, FB, Instagram, web, SMS, email | Bangla voice calls, CRM, canned replies, notes, routing, usage alerts |
| **ChatBD** (BD) | ৳999 / 2,499 / 4,999 | WhatsApp-first | Broadcasts, tags, analytics, cheap entry plan |
| **Revora** (was MyAlice) | ~৳18,000+ | WhatsApp, Messenger, Instagram, TikTok, web, email | Abandoned carts, WhatsApp campaigns, CSAT, hiding bad comments |
| **ManyChat** (global) | $14 – 139 | Instagram, Messenger, WhatsApp, TikTok | Comment and story triggers, follow-up sequences |

**What we already do better:**
- Nothing is saved without a read-back and a yes.
- Python works out every price.
- Bangla voice in the browser, and confirmation calls.
- Four kinds of business, not just shops.
- Photo search, WooCommerce and Google Sheet sync.
- Payments through the shop's own SSLCommerz.
- Moderators with an activity log.

Keep these in the pitch.

## The gaps, most worth it first

### Must have: buyers and shops expect these

- [x] **G1 Comment-to-DM on Facebook posts and ads** · M · Meta
  - *Who has it:* Sellzy, LazyChat, Revora, ManyChat.
  - *Why:* f-commerce buyers comment "দাম কত?" under a post far more often than they inbox
    first. A comment left without a reply is a lost sale. Sellzy sells this first.
  - *Smallest build:* subscribe the Page's `feed` webhook. For a new comment, send one private
    reply (Graph API private replies) that opens a normal Messenger conversation with the post's
    product, plus one short public reply ("Inbox e details dilam"). From then on it's our usual
    Messenger chat.
  - *Needs:* `pages_manage_engagement` and `pages_read_user_content` through App Review.
  - 26 Oct, D141: built (day R15); App Review and a real post are next.
- [x] **G2 Voice notes on Messenger and Telegram** · S–M *(built 26 Oct, D147, WhatsApp too)*
  - *Who has it:* LazyChat.
  - *Why:* many BD buyers send voice notes instead of typing. Today they get "I can only read
    text messages" ([channels.py](../src/heydesk/services/channels.py) `NOT_TEXT`).
  - *Smallest build:* download the audio attachment. Convert it to 16 kHz WAV (PyAV or ffmpeg)
    and send it to the speech-to-text we already have (`WhisperSTT` / Deepgram). Then run the
    turn as if they had typed the words. The reply stays text.
- [ ] **G3 Instagram DMs** · M · Meta
  - *Who has it:* Sellzy, LazyChat, ARA, Revora, ManyChat.
  - *Why:* many fashion and beauty shops sell on Instagram as much as on Facebook.
  - *Smallest build:* Instagram messaging arrives through the same Graph webhook as Messenger,
    so it's a new `kind` in `channels` that reuses the Messenger adapter. Comments on Instagram
    come after G1 works.
  - *Needs:* `instagram_basic` and `instagram_manage_messages`.
- [x] **G4 Owner's dashboard** · S *(built 26 Oct, D144)*
  - *Who has it:* LazyChat, ChatBD, Revora, ARA.
  - *Why:* an owner pays for results they can see. Today only the admin sees numbers (D72).
  - *Smallest build:* a `GET /api/stats?days=7` route that adds up rows we already have:
    conversations, orders or bookings and their total, how many chats ended in an order, and
    hand-offs. Add one card row on the owner's home page.
- [x] **G5 Plans, usage and billing** · M *(built 22 Oct, D106; plan changes D116)*
  - *Who has it:* everyone. ARA warns at 80% of the quota.
  - *Why:* we can't charge without it. The tiers are in the 22 Oct pricing research (Starter
    ৳1,490, Growth ৳3,990, Pro ৳8,990, metered by AI replies).
  - *Smallest build:* a `plan` on the workspace that sets its monthly reply cap. The cap reuses
    the existing daily limit (`WORKSPACE_DAILY_TURNS`, D51) with a monthly count from `turns`.
    The owner sees "1,240 of 1,500 replies" and gets an email at 80%. They pay by bKash or card
    through *our* SSLCommerz store (the shop's own store code already exists), and the agent
    pauses after a 3-day grace period.
- [x] **G6 Export orders** · S
  - *Who has it:* Sellzy (CSV and Google Sheets).
  - *Why:* owners copy orders into their courier's bulk upload and their accounts.
  - *Smallest build:* `GET /api/shop/orders.csv` (and the same for restaurant orders,
    bookings and appointments), using the list's current filter.
  - 26 Oct, D131: built (day R4), with the inbox's *Export*; opened in LibreOffice.

### Should have: they help a shop sell more

- [x] **G7 A nudge for an order not finished** · S–M
  - *Who has it:* Revora (abandoned cart), Sellzy ("recovers late-night orders").
  - *Why:* a buyer who got the read-back but never said yes is the warmest lead a shop has.
  - *Smallest build:* a timed round finds conversations whose last tool result was a read-back
    with no order, 1–3 hours later and still inside Messenger's 24-hour window. It sends one
    message ("Apnar order ta confirm korbo?"). Send only one, never twice. Telegram has no
    window.
  - 26 Oct, D139: built (day R13), in the snooze round; the line is in English, like D134's.
- [x] **G8 Duplicate-order guard** · S *(built 26 Oct, D146)*
  - *Who has it:* Sellzy.
  - *Why:* the same buyer ordering twice in a few minutes is usually a mistake or a prank, and
    it costs a delivery charge.
  - *Smallest build:* `place_order` checks for an order from the same mobile with the same items
    in the last `DUPLICATE_HOURS`. If it finds one, the tool returns `ok=False` with a hint,
    and the model asks whether it's a second order.
- [x] **G9 Inbox tools: labels, internal notes, saved replies** · M
  - *Who has it:* LazyChat (labels, filters), ARA (notes, canned replies, Open/Resolved).
  - *Why:* once moderators run the inbox (D97), they need to sort it and reply fast in the
    same words every time.
  - *Smallest build:*
    - `labels` as a JSON list on `conversations`, with a filter.
    - A `notes` table (insert-only, rule 5) that the visitor never sees.
    - A list of saved replies in the agent's config.
  - 26 Oct, D136: built (day R10), with a reply box in the inbox.
- [ ] **G10 Complaints flagged** · S
  - *Who has it:* LazyChat (a complaints section), ChatBD and ARA (sentiment).
  - *Why:* an angry buyer left with the bot becomes a bad review on the Page.
  - *Smallest build:* a shared tool `flag_complaint(reason)` that the prompt tells the model to
    call. It hands off like today (D43) with the reason "complaint", and the inbox filters by it.
    No sentiment model.
  - 26 Oct, D138: built with G37 (day R12).
- [ ] **G11 Owner alerts besides Telegram** · S
  - *Who has it:* LazyChat (mobile app), ARA (email).
  - *Why:* many owners don't use Telegram (product-gaps §6).
  - *Smallest build:* the same alerts (new order, hand-off) by email through Brevo, which we
    already use for signup codes. The owner picks Telegram, email or both. A mobile app or web
    push comes later.
- [x] **G12 WhatsApp** · L · Meta *(built, D103; live check still open)*
  - *Who has it:* Sellzy, LazyChat, ChatBD, Revora, ARA.
  - *Why:* WhatsApp is growing in BD, and ChatBD is built around it.
  - *Smallest build:* the Cloud API's webhook works like Messenger's, so it's another
    `channels.kind`. Replies inside the 24-hour window are free. A message the business starts
    must be a paid, approved template, so G7 and G13 cost money on WhatsApp.
  - *Needs:* a business phone number and Meta Business Verification. Ask two or three owners
    whether their buyers use it before building.

### Nice to have: later, or when a customer asks

- [ ] **G13 Broadcasts** · M · Meta
  - *Who has it:* LazyChat, ChatBD, Revora, ManyChat.
  - *Why:* "new Eid collection" messages to past buyers.
  - *Catch:* Messenger allows almost nothing outside the 24-hour window without Meta's paid
    marketing messages, and WhatsApp needs paid templates. Telegram is free. Build it for
    Telegram first, or skip it.
- [x] **G14 AI draft replies for moderators** · S–M
  - *Who has it:* ARA.
  - *Why:* when a moderator takes a chat over, a suggested reply saves typing.
  - *Smallest build:* a button in the conversation that runs the model with no tools and shows
    the text. Nothing is sent until the moderator sends it.
  - 26 Oct, D137: built with G36 (day R11).
- [ ] **G15 Typing indicator and answering hours** · S
  - *Who has it:* Sellzy (typing indicator, natural delays, always-on or set hours).
  - *Smallest build:*
    - Send Messenger's `typing_on` and Telegram's `sendChatAction` while a turn runs.
    - An owner setting so the agent answers only outside the shop's own hours. Some owners
      want to reply themselves by day.
- [ ] **G16 Suggesting a second product** · S
  - *Who has it:* LazyChat and Revora (recommendations), Sellzy (upsell).
  - *Smallest build:* an optional `goes_with` list of codes on a product. `update_cart` returns
    it, and the prompt offers it once. Don't add a recommendation engine.
  - 25 Oct, D125: built (day R2); the live check on a real Page is next.
- [ ] **G17 A customer's history** · M
  - *Who has it:* ARA (Customer 360), ChatBD (contacts and tags).
  - *Smallest build:* a buyers list grouped by mobile, with their orders, channels and
    conversations, from the tables we already have.
  - 26 Oct, D132: a shop's buyers list (day R5); restaurants and clinics are next.
- [x] **G18 Appointment and booking reminders** · M *(built 26 Oct, D145; Messenger and WhatsApp only inside the 24-hour window)*
  - *Who has it:* ARA (calendar sync and confirmations), Edesy (clinics).
  - *Why:* no-shows cost clinics and restaurants.
  - *Smallest build:* a timed round sends a reminder the day before on Telegram. On Messenger,
    send it only if Meta's current rules allow a confirmed-booking update outside the window.
    Check that first.
- [ ] **G19 More than one Page or brand per business** · M
  - *Who has it:* Sellzy (৳500 for each extra Page), ARA (multi-brand).
  - *Why:* bigger sellers run two or three Pages. This would be a paid add-on later. Today it's
    one agent per business (D22).
- [ ] **G20 Shopify** · M
  - *Who has it:* LazyChat, Revora.
  - *Why:* few BD f-shops use it; WooCommerce (D75) covers most websites. Build it only if a
    paying customer has Shopify.

## From the "AI Practor" reel (24 Oct)

A Bangla sales reel from a BD competitor (name as heard: "AI Practor"). They sell an
inbox, an agent and an order system with **no monthly fee**, ৳0.05–0.20 per AI reply. Below is
only what we don't have yet. What the reel shows that we **already have**: Messenger, WhatsApp
and web chat; photo search; buttons for choices; cards; stock; changing or cancelling an order
before it's confirmed (`change_order`, `cancel_order`); "where's my parcel" (`track_order`);
courier booking with status news to the buyer (D112, D113); remembering a returning buyer
(D114); refusing off-topic talk (`off_topic`); bookings for a service business; billing per reply.

### Next: cheap, and they make the agent feel human

- [ ] **G21 Many messages, one reply** · S
  - A buyer sends "hi" / "dam koto" / a photo in three bubbles. Wait ~3 s after the last one,
    then run one turn on all of them. Today each bubble is its own turn and its own reply.
  - *Smallest build:* in `ChannelService._answer`, a per-chat debounce (an `asyncio` task that
    is reset by each new message). In memory is fine for one process.
- [ ] **G22 "Seen", then typing, on Messenger** · S (finishes G15's first half)
  - Telegram and WhatsApp already show typing. Messenger needs `mark_seen` + `typing_on`.
- [ ] **G23 A reply to an earlier message** · S
  - When the buyer replies to one of their old messages (Messenger `reply_to.mid`, Telegram
    `reply_to_message`, WhatsApp `context.id`), put the quoted text before theirs:
    `(replying to: "…")`. A quoted photo is searched as a photo.
- [ ] **G24 Sir or ma'am** · S · prompt only
  - One platform line: address the buyer as the shop does (Bhaiya/Apu, Sir/Ma'am), and keep it
    once known. Goes in docs/prompt-rules.md.

### Then: the shop sells without a website

- [ ] **G25 Product cards side by side** · M
  - `find_product` returns a `products` card: up to 10, each a photo, name, price and two
    buttons, *I want this* (sends the product's code as the tap's text) and *Details*.
    Messenger generic template carousel, WhatsApp list, Telegram one photo each, web chat a row.
  - 25 Oct, D125: built (day R2): up to 10; Telegram and WhatsApp send the first 3 photos,
    then one message to tap. The live check on a real Page is next.
- [ ] **G26 A product page and an order page** · M
  - `/p/<code>`: photos, price, sizes, delivery time, and *Buy now* → a short order form →
    saved through the same `Confirmed[T]` path (the form's submit is the yes). The receipt
    card is sent back into the buyer's chat. `/o/<id>`: the order's status page, linked from
    the receipt. Server-rendered, no SPA.
  - 25 Oct, D121: the product page, linked from every product card (Day R3a). *Buy now* and
    `/o/<id>` are next (Day R3).
  - 26 Oct, D130: built (day R3): *Buy now*, the check page and *Place order* through
    `confirm`, the receipt back in the chat, `/o/<id>`. The live check from Messenger is next.
- [ ] **G27 Videos and more photos per product** · S
  - `videos` (links) on a product; `show_photos` can send them. A how-to video is just a link.
  - 25 Oct, D125: built (day R2); the live check on a real Page is next.
- [ ] **G16** (suggest a second product) moves up here.

### Then: orders the team runs

- [x] **G28 More order steps and money in** · M
  - Statuses today: new, confirmed, shipped, delivered, returned (+ cancelled). Add *on hold*,
    *packed* (ready to ship), and on delivered/returned a `paid_in` flag and a `reason`
    (returned, damaged, refused, cancelled because…). Resist the reel's eight steps: add one
    only when an owner asks.
  - 26 Oct, D131: built (day R4): on hold, packed, money in, a reason.
- [ ] **G29 A buyer's record before shipping** · M
  - By mobile: our own orders, returns and cancels for this shop, plus the courier's fraud
    record (Steadfast has a fraud-check endpoint; Pathao doesn't publicly). Shown on the order
    as "3 orders, 1 returned". Overlaps G17.
  - 26 Oct, D132: built (day R5) from our own orders; the courier's fraud check is left out
    (Steadfast has no public endpoint for it).
- [x] **G30 Orders typed by the team** · S
  - The owner's screen can add an order for a buyer who called. It skips the yes (the team is
    the yes), so it needs its own writing method; say so in decisions.md.
  - 26 Oct, D131: built (day R4): *New order* on the inbox, `add_team_order`.
- [ ] **G31 Coupons** · M
  - A `coupons` table: code, % or fixed or free delivery, end date, max uses. `update_cart`
    takes `coupon`; Python checks it and puts the discount on the bill (rule 1).
  - 26 Oct, D133: built (day R6), with a Coupons page; the dev chat check with the real model
    is next.
- [x] **G32 Delivery-day reminder and a review request** · S
  - Two more lines in the courier round: on "out for delivery", remind the buyer; a day after
    delivered, ask how it was (one button row: 👍 / 👎 / write). Only inside each app's window.
  - 26 Oct, D134: built (day R7); the reminder needs the courier's word for "out for
    delivery", so only the fake courier gives it yet.
- [ ] **G6** (export orders) stays here.

### Then: the inbox

- [ ] **G33 Take over with a dot** · M · Meta
  - The owner types "." from Messenger or Business Suite → the agent stops for that chat; "."
    again, or 30 min of quiet, hands it back. Needs Messenger's `message_echoes` field (our own
    Page's messages arrive as echoes; ignore the ones we sent). The agent sees the owner's
    words in the history next turn (rule 5: they're added, not edited).
  - 26 Oct, D135: built (day R8), with the inbox's *Take over* and "." on Telegram; the live
    check on a real Page (connected again, for its echoes) is next.
- [ ] **G34 Inbox: search, done, snooze, assign** · M (with G9)
  - Search names, phones, order codes and message text (`ILIKE` first; Postgres full text
    later). *Done* hides a chat until the buyer writes again. *Snooze until* brings it back with
    an alert. *Assign* to a moderator, who gets the alert. G9's notes and labels go with it.
  - 25 Oct, D126: *Assign* built (day I1), without the alert; D128: search, done and snooze
    built (day I3). Their browser checks are next.
- [x] **G35 The buyer beside the chat** · M (= G17)
  - The chat screen's right side: their orders, bookings, notes. Built on G17's list.
  - 26 Oct, D136: built (day R10): a shop's buyer record, what the chat took, labels, notes.
- [x] **G36 AI help for the moderator** · S–M (G14, bigger)
  - Three buttons on a chat: *Summary*, *Ask about this chat*, *Draft a reply*. The model with
    no tools; nothing is sent until the moderator sends it.
  - 26 Oct, D137: built (day R11).
- [ ] **G37 Complaints become tickets** · M (G10, bigger)
  - `flag_complaint(reason)` hands off and opens a ticket: who has it, open/solved, how old.
    One table, one list screen.
  - 26 Oct, D138: built (day R12); the dev chat check with the real model is next.
- [ ] **G38 AI off for one buyer** · S
  - A spammer is handed off by `off_topic`; the owner taps *Turn AI off for them* and their
    messages are stored but never run (and never billed). Same switch as "Later: blocked
    visitors".
  - 25 Oct, D127: built (day I2): Block, AI off, and a flood blocks by itself; the dev chat
    check is next.

### Then: follow-ups and leads

- [x] **G7** (nudge an unfinished order) comes first here. (26 Oct, D139)
- [x] **G39 Leads with a callback time** · M
  - A `take_lead(when, note)` tool: "call me Friday 5pm" → a lead with that time (Python reads
    the date, rule 1). A leads list; the team taps the outcome (sold / not now / lost: why).
    The *why* counts become "why we lose sales".
  - 26 Oct, D139: built (day R13): `take_lead` reads back before a yes; `/api/leads/why`
    counts the whys. The dev chat check and the screen are next.
- [x] **G40 An offer to everyone who wrote today** · M · Meta
  - The owner writes "free delivery for the next 12 hours" and sends it to everyone who wrote
    in the last 24 h (inside Messenger's window, so it's free). The offer is saved with its
    end time and who got it; `update_cart` applies it only to them, only until then. Shares
    G31's discount code path. This is the safe half of G13.
  - 26 Oct, D140: built (day R14): a coupon with `only_for`; the dev chat check is next.

### Then: comments (all need G1 first)

- [x] **G1** first: one product's post → reply in the comment and in the inbox.
- [x] **G41 Many products in one post** · S after G1
  - The comment gets the price range; the inbox gets the post's products as G25's cards.
  - Needs a post → products map: the owner tags it, or we match the post's photos (D57).
  - 26 Oct, D142: built (day R16): found once from the post's words or photo and kept.
- [x] **G42 One more nudge under the comment** · S after G1
  - If they don't answer the inbox in ~2 h, one more comment reply. Once.
  - 26 Oct, D142: built (day R16).
- [x] **G43 One buyer across comments and inbox** · S after G1
  - A comment's sender id is the same Page-scoped id as their Messenger chat, so the comment
    goes into that conversation's history. Mostly free once G1 stores it there.
  - 26 Oct, D141: built with G1: the private reply's PSID is the chat's contact.

### Then: teaching the agent

- [x] **G44 Questions it couldn't answer** · S
  - When `search_knowledge` finds nothing or the turn hands off for "don't know", save the
    question. A list grouped by likeness with a count, and a *Teach it* box that adds a
    knowledge entry.
  - 26 Oct, D143: built (day R17); grouped by word likeness, no model.
- [ ] **G45 Change it in plain words, try it, then save** · M
  - The owner writes "say the delivery price before the total". The model rewrites the agent's
    editable prompt section; the owner sees a test chat with the new version, then *Save* or
    *Undo*. Keep the last few versions on the agent (this bends D15's "no versions", so it needs
    a decision).
- [x] **G46 Practice mode** · S (removed again, D166)
  - A new agent answers only the owner's own test accounts (listed ids) until they tap *Go live*.
  - 26 Oct, D143: built (day R17), on the messaging apps.
- [x] **G4** (owner's dashboard) goes here, plus: AI-handled vs handed-off, and where each order
  came from (chat, product page, team).
  - 26 Oct, D144: built (day R19).

### Service businesses

- [x] **G18** (reminder the day before) and **G47 Advance payment's TrxID** · S: the booking
  takes the bKash/Nagad transaction id the buyer typed, and the owner ticks it as checked.
  - 26 Oct, D145: built (day R20), the TrxID on a clinic's appointments.

### Not copying

- **Instagram** stays G3 (Meta review); **WhatsApp on the shop's own number** needs the Cloud
  API's coexistence onboarding, check before promising it.
- **A Chrome extension for Business Suite.** G33's dot covers taking over from there.
- **No monthly fee, pay per reply.** A pricing choice, not code: our metering (D106) could sell
  it as a plan. Decide on price, not here.
- **"Advanced caching, 24 hours."** That's the model provider's prompt cache. Our prompt is
  built every turn (D15) with the date and time at the end, so the start is already stable and
  cacheable; check whether Maira or Groq bill cached tokens less before doing anything.
- **Asking for features inside the app.** A link to a form or Telegram is enough.
- **Unlimited teammates.** A plan limit, not a feature.

## Decided against (for now)

- ~~**Courier booking**~~ is back: Steadfast and Pathao behind the courier port (D112, D113),
  with status news sent to the buyer. The courier's fraud record is G29.
- **Calls out to phone numbers, and payment-reminder calls** (ARA). There's no free and legal
  way to place automatic calls to BD mobiles (product-gaps §3). Our confirmation call is a link
  to the browser voice call (D88, D89).
- **SMS as a channel** (ARA). It costs per message and buyers don't chat by SMS. SMS is only
  worth it for codes (product-gaps §2).
- **TikTok comments, hiding bad comments, a visual flow builder** (Revora, ManyChat). A flow
  builder is the opposite of our model-talks, code-decides design. The other two can follow G1
  if customers want them.
- **An owner's mobile app** (LazyChat). G11's email and Telegram alerts come first. Revisit as
  a PWA once owners are paying.

## Sources

- [Sellzy](https://sellzybd.com) ·
  [Sellzy's buying guide](https://sellzybd.com/blog/how-to-choose-ai-sales-agent-bangladesh-2026)
- [LazyChat](https://lazychat.io)
- [ARA, SysSolution](https://syssolution.net/ara)
- [ChatBD](https://www.chatbd.app/)
- [Revora (was MyAlice)](https://userevora.com/)
- [ManyChat review 2026](https://www.eesel.ai/blog/manychat)
- [BD AI tools list, Aug 2026 (Omnistra)](https://www.omnistra.io/blog/best-ai-tools-bangladesh-2026)
- [Edesy, Bengali clinic voicebot](https://edesy.in/hospital-voicebot/languages/bengali)
