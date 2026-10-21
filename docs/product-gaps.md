# Is HeyDesk a real product yet? Gaps and fixes

Written 18 Oct 2026. Researched, not built: nothing here is in the code yet. Each fix
needs a go-ahead, and a design change also needs a line in decisions.md.

**Short answer:** the core is real. It has a read-back before every save, Python works out
the prices, it has four desks and four channels, and a real order reached WooCommerce. What
stops a real Bangladeshi shop using it is mostly **outside the code**: language, phone numbers,
telephony rules and Meta's review. Five of the gaps below can be fixed in small steps. One,
free phone calls, has no answer that is both free and legal at scale, and this report says so.

| # | Gap | How bad | Cheapest real fix |
|---|---|---|---|
| 1 | English-only voice | Blocks voice in BD | Sarvam or Soniox to hear, Azure or Google to speak (bn-BD) |
| 2 | No phone verification | Fake COD orders | Telegram/Messenger share the number for free; SMS OTP only on web |
| 3 | Scheduled calls aren't free | Feature is unusable | A "tap to talk" link to the voice call we already have; a SIM gateway for one pilot only |
| 4 | Tokens: ~6–13k per turn | Hits Groq's free limits fast | Groq's cache, and older tool results cut from the history |
| 5 | Maira doesn't stream: 4–6 s per reply | Voice feels dead | Groq for voice; Maira for text only |
| 6 | Meta App Review | Can't connect other shops' Pages | Business Verification + App Review, started early |
| 7 | No test set of real conversations | Can't pick a model or prove quality | 30 scripted chats in Bangla, Banglish and English |
| 8 | No money side | Not a business yet | Costs per conversation are known now (below); a price and bKash later |

---

## 1. Bangla speech (hearing and speaking)

*Built 19 Oct as D84–D87: Bangla calls by default (`VOICE_LANGUAGE`), our Bangla voice, and
our Whisper on a free Kaggle GPU. The rest of this section is the research as written.*

**Now:** Deepgram (English) or Chorus (`language="en"`) hears; Piper speaks with an
English voice (`en_US-lessac-medium`, [voice/call.py](../src/heydesk/voice/call.py)).
Text chat already handles Bangla, because the model reads it. Voice doesn't.

**What I found:** there is a fresh, independent Bangla benchmark (Sept 2026, 1,001 real clips, run
from Bangladesh) that tested every major API:

| Hearing (streaming) | Error per character | Final words after speech ends | Notes |
|---|---|---|---|
| Soniox `stt-rt-v5` | 7.4% | 2.5 s | Most accurate, but slow to finish |
| **Sarvam `saaras:v3`** | 9.0% | **0.9 s** | Accurate *and* fast; Pipecat has `SarvamSTTService` |
| Deepgram `nova-3` (`language=bn`) | 17.5% | 1.6 s | What we use; a one-line change to try |
| Google `chirp_2` | 23.6% | 2.2 s | |
| ElevenLabs realtime | 38.6% | 1.4 s | |

Caveats from the benchmark itself: no phone audio, no regional dialects, and no Bangla–English
mixing. Our callers mix all the time, so we should test on our own recordings before we choose.

For **speaking** Bangla:

- **Azure**: `bn-BD-NabanitaNeural` (female) and `bn-BD-PradeepNeural` (male), a Bangladeshi
  accent. Pipecat has `AzureTTSService`. There is a free tier of characters each month.
- **Google Gemini-TTS**: has `bn-BD`. Chirp 3 HD only has `bn-IN` (the Kolkata accent).
- **Sarvam Bulbul v3**: Bengali, but Indian (`bn-IN`), ₹30 per 10,000 characters, and new
  accounts get ₹100 free. Pipecat has `SarvamTTSService`.
- **Free and local (open source)**: small VITS models run on the MacBook's CPU fast enough for a
  call: `EMTIAZZ/bangladeshi-bangla-tts-vits` (Apache 2.0, trained for a Bangladeshi accent)
  and Coqui's `tts_models/bn/custom/vits-male` / `vits-female` (Apache 2.0, MOS 4.4–4.5 in the
  BanglaTalk paper, but trained on IIT Madras data, an Indian accent). Meta's `mms-tts-ben` scored
  lowest (3.66) and is non-commercial. The bigger models (Orpheus 3B Bangla, Indic Parler-TTS,
  IndicF5) sound better but need a GPU and are too slow for a live call on a Mac. Run the model
  as its own small server; HeyDesk calls it by URL. Not on Colab: its FAQ forbids "web service
  offerings" on every plan. If it needs a GPU later, Modal or a rented GPU are allowed to serve.

**Recommendation:** use Sarvam to hear (it's the most accurate *and* the fastest) and Azure
`bn-BD` to speak. Add a `language` on the agent (`en` or `bn`) and choose the STT and TTS from
it. `speakable()` would also need Bangla number words ("চারশো পঞ্চাশ টাকা"). That's real work,
because Bangla numbers 1–99 are irregular and are a lookup table, not rules.

**The model has to cope with Bangla too.** Maira already "mixed Bangla into English replies" on
W5. Customers will write Banglish ("lal ta koto?"). Keep the prompt in English and add one
platform rule: reply in the language and script the visitor used. Bangla also costs about
1.5× the tokens of the same English on gpt-oss's tokenizer.

## 2. Verifying a phone number

**Now:** a phone is checked only for shape (11 digits from 01). Anyone can type any number.

**What BD shops actually fear** is the fake COD order: a parcel sent out and refused. The shop
already makes a confirmation call (D45). So verification is worth the most on the **web chat**,
where we know nothing about the visitor.

Free options, by channel:

| Channel | Free way to get a real number | How sure |
|---|---|---|
| Telegram | A "Share my phone" button (`request_contact`). Telegram sends the account's own number; check `contact.user_id == from.id` | Verified by Telegram |
| Messenger | The `user_phone_number` quick reply: one tap shares the number on their Facebook profile (still in Meta's docs, April 2026) | It's their profile's number, not proven by a code |
| Web / voice | An SMS code (OTP) | Verified |

For SMS on the web:

- **Free (for the demo and a pilot):** an old Android phone with a SIM, running the open-source
  *SMS Gateway for Android* (capcom6, Apache 2.0). It gives a small REST API on the local
  network. The SIM's own SMS pack pays for it. Don't send thousands from a personal SIM:
  operators watch for that.
- **Cheap (for real):** a BD SMS company (Alpha SMS, BulkSMSBD, SSL Wireless), about
  ৳0.25–0.40 per non-masking OTP. That's under ৳40 for 100 orders.
- **Telegram Gateway:** $0.01 a code, sent inside Telegram. Cheaper than SMS for people who
  have Telegram, but many BD buyers don't.

**A better signal than an OTP for shops: courier history.** Bangladeshi shops check a phone
number's record with Steadfast, Pathao and RedX ("12 delivered, 3 returned"). Open-source checkers
exist (`courier-fraud-checker-bd` on PyPI). But they log in to the courier portals with the shop's
own account, can break when a portal changes, and RedX now needs an OTP to log in. We already warn
the owner about a phone with past returns *in our own data* (D54). Showing the courier ratio in the
same alert would be the feature shop owners would notice most. Check each courier's terms first.
*Built for Steadfast through its official API (D91), then removed with the courier link (D95).*

**Recommendation:** the Telegram button and the Messenger quick reply first (free, a day's work,
no new service). Then an OTP only for web orders over a set amount, behind an `Sms` port with an
Android-gateway adapter and a fake.

## 3. Scheduled (outbound) calls, free and local

**Why Telnyx/Twilio fall short:** there's no Bangladeshi number, so the call shows a foreign
caller ID that people don't answer. They charge per minute. And Twilio's trial only calls
numbers you have verified.

**The hard truth:** in Bangladesh, every automatic call to a mobile number goes either through
a licensed operator (and costs per minute) or through a SIM in a gateway box. **BTRC treats SIM
boxes as illegal VoIP.** In July 2026 it fined four operators Tk 8.7 crore over 8,682 such SIMs,
and the operators run SIM-box detection that blocks numbers that act like gateways. So there is
**no option that is free, legal and works for many businesses.** What does exist:

| Option | Cost | Legal | Good for |
|---|---|---|---|
| **A. Don't phone: send a "tap to talk" link** on Telegram/Messenger/SMS that opens our existing browser voice call (WebRTC, already built) | **Free** | Yes | Every business, today |
| **B. One SIM in a GoIP-1 gateway**, Asterisk on our server, Pipecat through the community `pipecat-asterisk` serializer (Asterisk's WebSocket channel) | About $60–100 once, plus the SIM's call rate | Grey: a business calling its own customers from its own SIM is local, not international bypass, but detection can still block the SIM | The demo and one pilot shop |
| **C. A licensed IPTSP SIP trunk** (a BTRC-licensed `09` number) into Asterisk, then Pipecat | Per minute (old BTRC rule: ৳0.65–2 a minute to a mobile); check with a provider | Yes | When a business pays |
| **D. WhatsApp Business Calling API** | A call the customer starts is free; one the business starts is paid per minute | Yes | If BD shops move to WhatsApp |

A cheap USB 3G dongle (Huawei E1550 + `chan_dongle`) does the same as B for less money. But the
driver is alpha, and people report lost audio and modems dropping after calls. GoIP is the
reliable version of the same idea.

**Recommendation:** build **A** as "scheduled calling". At the set time, the agent sends the
customer a message ("Your order O-104 is ready to confirm: tap to talk to us") with a link to a
voice call that already knows the order. It costs nothing and needs no new telephony, and the
customer picks the moment. If they don't tap, the owner sees "not confirmed" and calls by hand,
as shops do now. Try **B** only for the demo or one pilot, knowing the risk, and keep it behind
the same `PhoneProvider` idea as frontdesk-api (D39) so that C can replace it.

## 4. Fewer tokens, the same experience

**Measured** (the `turns` table in `heydesk.db`, 74 Maira turns):

| Channel | Average input tokens | Largest | Average output |
|---|---|---|---|
| Web | 6,636 | 13,428 | 97 |
| Messenger | 9,432 | 15,567 | 53 |
| Telegram | 12,700 | 31,316 | 109 |

A turn with no tool averages **5,557** input tokens. A turn with one tool averages **12,160**,
because the whole prompt is sent twice. Output is about 1% of the tokens, so **input is the whole
problem**. Of a shop's prompt, the tool schemas are about 1,800 tokens and the platform rules
about 400. The rest is the desk's prompt, the product list, the FAQ and the history. Telegram is
the largest because its conversations are long and every old tool result is sent again.

**Money is not the issue on Groq; limits and speed are.** At $0.15 per million input tokens, a
9,000-token turn costs about $0.0014, so a 10-turn chat costs about ৳1.7. But Groq's base limits
for gpt-oss-120b are **8,000 tokens a minute and 200,000 a day**. That's roughly *one turn a
minute and about 20 a day*, and one 12,000-token tool turn is already over the minute's limit.

Fixes, the biggest and simplest first:

1. **Use Groq's prompt cache (free, automatic).** An identical prefix costs half, and is
   faster, and **cached tokens don't count toward the rate limits**. To get hits, the start of
   the prompt has to be the same every call. Ours is: the platform rules and then the desk. But
   "Right now" (the time) sits at the end of the system message, so the history after it is never
   cached. Moving "Right now" to a short message *after* the history would make most of each call
   a cache hit. That changes rule 4's wording ("at the end"), so it's a design change.
2. **Send old tool results as one line.** Keep the last two turns' tool results whole, and turn
   older ones into something like `[find_product: 4 results]`. That cuts the long Telegram
   conversations most, and the model has already said what mattered.
3. **Shorter tool descriptions.** At 1,800 tokens, the shop's 8 tools are a third of a no-tool
   turn. Make the descriptions short and move the "how to" into the error hints the tools
   already return.
4. **Ask Gigalogy** whether Maira caches or has limits like these, and what it costs per token.
   Our traces show Maira's cost as 0 ("unknown", D71).

Not worth it: summarising the history with a second model call (it costs a call to save tokens),
or skipping the model for the starters (the replies would feel canned).

## 5. Maira is too slow for voice

From the same table, Maira's **time to the first word equals its total time**: 4.0–6.1 s on
average, because it doesn't stream (D16). On a phone call, that's 5 seconds of silence after
every sentence, and 10 seconds or more when a tool runs. Barge-in and sentence-by-sentence
speech don't help when nothing arrives until the end. Groq streams its first tokens in well
under a second.

**Recommendation:** run voice on Groq (a setting per channel, or `LLM_PROVIDER=groq` for the
demo), and keep Maira for text if Gigalogy wants it shown. Measure it with
`scripts/latency.py`, which is still open in PLAN.md.

## 6. Other things I found

- **Meta App Review blocks real shops on Messenger.** Without App Review, the Meta app only works
  for people who have a role on the app or the Page. To answer *other* businesses' Pages, it needs
  Business Verification and Advanced Access to `pages_messaging` through App Review, and that takes
  weeks. Start it before any pilot. Telegram has no such gate.
- **No test set of conversations.** Every quality check so far was a live run by hand. Thirty
  short scripted chats (Bangla, Banglish and English; an order, a cancel, a closed day, a "no")
  run against each model would show which model to use and catch a prompt change that breaks
  something. Without them, "it works" can't be proven to a mentor or a customer.
- **The money side.** Rough costs per conversation with today's prices: text on Groq is about
  ৳2. A 3-minute voice call is about ৳6–8 (Sarvam or Deepgram to hear, about $0.02; Azure to
  speak, about $0.02; the model, about $0.01). A shop could pay ৳1,000–2,000 a month and leave
  room to spare. Nothing charges a business yet. That's fine for the demo, but a real product
  needs a plan and a bKash or SSLCommerz payment of its own.
- **Owner alerts are Telegram-only.** Many BD shop owners live in Messenger and WhatsApp, not
  Telegram. Ask two or three real owners before building more there.
- **Hosting.** SQLite on one machine with a tunnel is fine for the demo. Postgres and a real
  host are already in PLAN.md's "Later".

## Suggested order after the demo (26 Oct)

1. Voice on Groq, and measure P50/P95 (fixes §5; nearly free).
2. Groq cache and old tool results trimmed (§4; a day).
3. Telegram's share-phone button and Messenger's phone quick reply (§2; a day).
4. "Tap to talk" links as scheduled calls (§3A; two days, mostly reusing voice).
5. Bangla voice: Sarvam STT, Azure bn-BD TTS, Bangla `speakable()` (§1; three to four days).
6. The 30-conversation test set (§6).
7. Start Meta Business Verification and App Review (paperwork, so start it early).

## Sources

- Bangla speech-to-text benchmark, Sept 2026: [riverbornai/bangla-speech-to-text-benchmark](https://github.com/riverbornai/bangla-speech-to-text-benchmark/releases/tag/v1.0.0)
- [Deepgram Bengali](https://deepgram.com/product/speech-to-text/bengali) ·
  [Sarvam pricing](https://docs.sarvam.ai/api-reference-docs/pricing) ·
  [Sarvam with Pipecat](https://docs.sarvam.ai/api-reference-docs/integration/build-voice-agent-with-pipecat) ·
  [Pipecat services](https://docs.pipecat.ai/api-reference/server/services)
- [Azure speech language support](https://learn.microsoft.com/sv-se/azure/ai-services/speech-service/language-support) ·
  [Google Gemini-TTS languages](https://docs.cloud.google.com/text-to-speech/docs/gemini-tts) ·
  [IndicConformer bn](https://huggingface.co/ai4bharat/indicconformer_stt_bn_hybrid_ctc_rnnt_large/raw/main/README.md)
- [Messenger quick replies](https://developers.facebook.com/documentation/business-messaging/messenger-platform/send-messages/quick-replies) ·
  [Messenger Platform overview (access levels, App Review)](https://developers.facebook.com/docs/messenger-platform/overview)
- [SMS Gateway for Android](https://www.everydev.ai/tools/sms-gateway-for-android) ·
  [Alpha SMS OTP prices](https://sms.net.bd/OTP_NonMasking) ·
  [Telegram Gateway](https://core.telegram.org/gateway) ·
  [courier-fraud-checker-bd](https://pypi.org/project/courier-fraud-checker-bd/)
- BTRC and illegal VoIP SIMs, July 2026: [Dhaka Tribune](https://www.dhakatribune.com/bangladesh/corruption/416444/btrc-fines-four-mobile-operators-tk8.68-crore-over) ·
  [IPTSP tariffs (older)](https://www.thedailystar.net/news-detail-130420)
- [GoIP with Asterisk](https://nerdvittles.com/finally-a-100-portable-pbx-introducing-goip-a-sip-gsm-gateway-for-asterisk/) ·
  [Pipecat Asterisk serializer](https://docs.pipecat.ai/api-reference/server/services/serializers/asterisk) ·
  [chan_dongle](https://github.com/bg111/asterisk-chan-dongle)
- [WhatsApp Calling API pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing/)
- [Groq prompt caching](https://console.groq.com/docs/prompt-caching) ·
  [Groq gpt-oss prices](https://groq.com/blog/gpt-oss-improvements-prompt-caching-and-lower-pricing) ·
  [Groq rate limits](https://console.groq.com/docs/rate-limits) ·
  [Bangla tokenisation cost](https://arxiv.org/pdf/2608.09046)
