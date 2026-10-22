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
- Nothing is saved without a read-back and a yes, checked by Jev.
- Python works out every price.
- Bangla voice in the browser, and confirmation calls.
- Four kinds of business, not just shops.
- Photo search, WooCommerce and Google Sheet sync.
- Payments through the shop's own SSLCommerz.
- Moderators with an activity log.

Keep these in the pitch.

## The gaps, most worth it first

### Must have: buyers and shops expect these

- [ ] **G1 Comment-to-DM on Facebook posts and ads** · M · Meta
  - *Who has it:* Sellzy, LazyChat, Revora, ManyChat.
  - *Why:* f-commerce buyers comment "দাম কত?" under a post far more often than they inbox
    first. A comment left without a reply is a lost sale. Sellzy sells this first.
  - *Smallest build:* subscribe the Page's `feed` webhook. For a new comment, send one private
    reply (Graph API private replies) that opens a normal Messenger conversation with the post's
    product, plus one short public reply ("Inbox e details dilam"). From then on it's our usual
    Messenger chat.
  - *Needs:* `pages_manage_engagement` and `pages_read_user_content` through App Review.
- [ ] **G2 Voice notes on Messenger and Telegram** · S–M
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
- [ ] **G4 Owner's dashboard** · S
  - *Who has it:* LazyChat, ChatBD, Revora, ARA.
  - *Why:* an owner pays for results they can see. Today only the admin sees numbers (D72).
  - *Smallest build:* a `GET /api/stats?days=7` route that adds up rows we already have:
    conversations, orders or bookings and their total, how many chats ended in an order, and
    hand-offs. Add one card row on the owner's home page.
- [ ] **G5 Plans, usage and billing** · M
  - *Who has it:* everyone. ARA warns at 80% of the quota.
  - *Why:* we can't charge without it. The tiers are in the 22 Oct pricing research (Starter
    ৳1,490, Growth ৳3,990, Pro ৳8,990, metered by AI replies).
  - *Smallest build:* a `plan` on the workspace that sets its monthly reply cap. The cap reuses
    the existing daily limit (`WORKSPACE_DAILY_TURNS`, D51) with a monthly count from `turns`.
    The owner sees "1,240 of 1,500 replies" and gets an email at 80%. They pay by bKash or card
    through *our* SSLCommerz store (the shop's own store code already exists), and the agent
    pauses after a 3-day grace period.
- [ ] **G6 Export orders** · S
  - *Who has it:* Sellzy (CSV and Google Sheets).
  - *Why:* owners copy orders into their courier's bulk upload and their accounts.
  - *Smallest build:* `GET /api/shop/orders.csv` (and the same for restaurant orders,
    bookings and appointments), using the list's current filter.

### Should have: they help a shop sell more

- [ ] **G7 A nudge for an order not finished** · S–M
  - *Who has it:* Revora (abandoned cart), Sellzy ("recovers late-night orders").
  - *Why:* a buyer who got the read-back but never said yes is the warmest lead a shop has.
  - *Smallest build:* a timed round finds conversations whose last tool result was a read-back
    with no order, 1–3 hours later and still inside Messenger's 24-hour window. It sends one
    message ("Apnar order ta confirm korbo?"). Send only one, never twice. Telegram has no
    window.
- [ ] **G8 Duplicate-order guard** · S
  - *Who has it:* Sellzy.
  - *Why:* the same buyer ordering twice in a few minutes is usually a mistake or a prank, and
    it costs a delivery charge.
  - *Smallest build:* `place_order` checks for an order from the same mobile with the same items
    in the last `DUPLICATE_HOURS`. If it finds one, the tool returns `ok=False` with a hint,
    and the model asks whether it's a second order.
- [ ] **G9 Inbox tools: labels, internal notes, saved replies** · M
  - *Who has it:* LazyChat (labels, filters), ARA (notes, canned replies, Open/Resolved).
  - *Why:* once moderators run the inbox (D97), they need to sort it and reply fast in the
    same words every time.
  - *Smallest build:*
    - `labels` as a JSON list on `conversations`, with a filter.
    - A `notes` table (insert-only, rule 5) that the visitor never sees.
    - A list of saved replies in the agent's config.
- [ ] **G10 Complaints flagged** · S
  - *Who has it:* LazyChat (a complaints section), ChatBD and ARA (sentiment).
  - *Why:* an angry buyer left with the bot becomes a bad review on the Page.
  - *Smallest build:* a shared tool `flag_complaint(reason)` that the prompt tells the model to
    call. It hands off like today (D43) with the reason "complaint", and the inbox filters by it.
    No sentiment model.
- [ ] **G11 Owner alerts besides Telegram** · S
  - *Who has it:* LazyChat (mobile app), ARA (email).
  - *Why:* many owners don't use Telegram (product-gaps §6).
  - *Smallest build:* the same alerts (new order, hand-off) by email through Brevo, which we
    already use for signup codes. The owner picks Telegram, email or both. A mobile app or web
    push comes later.
- [ ] **G12 WhatsApp** · L · Meta
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
- [ ] **G14 AI draft replies for moderators** · S–M
  - *Who has it:* ARA.
  - *Why:* when a moderator takes a chat over, a suggested reply saves typing.
  - *Smallest build:* a button in the conversation that runs the model with no tools and shows
    the text. Nothing is sent until the moderator sends it.
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
- [ ] **G17 A customer's history** · M
  - *Who has it:* ARA (Customer 360), ChatBD (contacts and tags).
  - *Smallest build:* a buyers list grouped by mobile, with their orders, channels and
    conversations, from the tables we already have.
- [ ] **G18 Appointment and booking reminders** · M
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

## Decided against (for now)

- **Courier booking and the courier's fraud record** (Sellzy). We built this as D90/D91 and
  removed it in D95: owners book couriers in their own panels. Revisit if several paying shops
  ask. It's the one feature where Sellzy beats us on order handling.
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
