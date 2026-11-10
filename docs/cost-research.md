# HeyDesk running costs: estimate and pricing

Redone 4 Nov 2026. **$1 ≈ ৳123**, with a 5% buffer on dollar bills for the bank rate and card margin
(my assumption). Every figure comes from one small script, with its own checks.

How sure each price is:
- **Vendor**: read on the vendor's own page today.
- **3rd party**: the vendor's page didn't load or doesn't publish it. Check before you sign.
- **Assumed**: my estimate, with no source. These are listed in §9.

## 1. The answer

| | Pilot, 10 shops | Launch, 100 shops | Growth, 300 shops |
|---|---|---|---|
| **Fixed bills a month** | **৳6,000** | **৳7,500** | **৳15,400** |
| **AI, per reply: expected** | ৳0.07 | ৳0.07 | ৳0.07 |
| **AI, per reply: what we budget** | ৳0.15 | ৳0.15 | ৳0.15 |
| AI, per reply: worst case | ৳0.24 | ৳0.24 | ৳0.24 |

**Pricing verdict: keep all three prices. Lower one limit, Pro from 35,000 to 30,000 replies** (§7).

Break-even is **about 90 shops** with ৳2 lakh of marketing and a ৳1.2 lakh team, and **3 shops** on servers
alone. Reaching it takes about **৳15 lakh** (§8).

## 2. Tokens: what our traces show

From `heydesk.db`, 120 turns on real channels, all on Maira:

| Replies that used | Share | Input tokens |
|---|---|---|
| No tool | 33% | 5,191 |
| One tool | 58% | 9,847 |
| Two tools | 8% | 14,950 |
| **Average** | | **8,720**, or **1.75 model calls** at about 5,000 each |

Output averages 115 tokens. The slowest 10% of replies use 12,500+ input tokens.

**What this changes.** The logged input is the sum of every model call in a reply. The earlier
estimate treated a reply as one call. Later calls in a reply start seconds after the first, so they
always hit the cache.

**The bigger finding: the cache depends on the gap between replies.** The 5-minute cache only stays
warm if the same shop gets another reply within 5 minutes. The earlier estimate assumed about 85% of
every later reply was a hit. For a small shop that's wrong.

Priced on Claude Haiku 5.5 ($0.10 in, $0.50 out per million; cache read 0.1×, 5-minute write 1.25×,
1-hour write 2×), assuming 6,480 Claude tokens a call and 400 output with thinking on:

| Shop's replies | No cache | 5-minute cache | 1-hour cache |
|---|---|---|---|
| Trial, 14 a day | ৳0.164 | ৳0.128 | ৳0.102 |
| Starter half used, 50 a day | ৳0.164 | ৳0.114 | **৳0.060** |
| Starter full, 100 a day | ৳0.164 | ৳0.099 | **৳0.056** |
| Growth half used, 200 a day | ৳0.164 | ৳0.080 | **৳0.056** |
| Pro, 500+ a day | ৳0.164 | ৳0.060 or less | ৳0.056 |

**Decision: use the 1-hour cache on the stable part of the prompt.** It halves the bill for the small
shops, who are most of our customers. Keep the channel and time after the cache mark, as the prompt
already does, or nothing will hit.

Other models, for comparison:
- Groq gpt-oss-120b, the fallback if Haiku's Bangla isn't good enough: about ৳0.19 *(3rd party, from
  the 2 Nov research; Groq's page didn't load today)*.
- Gemini 3.5 Flash-Lite: $0.30 in, $2.50 out *(Vendor)*, about ৳0.50 a reply. It's no longer cheap.
- Maira: price still unknown. **Ask Gigalogy**, since it's our default.

## 3. The all-in cost of a reply

| Part | Per reply |
|---|---|
| Model, with the 1-hour cache | ৳0.056 |
| Retries and the team's AI help, +6% (from traces) | ৳0.003 |
| The second AI checking a yes: 10% of replies, 2,000 tokens | ৳0.003 |
| Voice notes: 10% of messages, 15 seconds, Soniox | ৳0.005 |
| Embeddings: search on 30%, a buyer's photo on 5% | ৳0.001 |
| 5% dollar buffer | ৳0.003 |
| **Expected** | **৳0.07** |

The **worst case is ৳0.24**: no cache hits, every chat as long as the slowest 10%, 700 output tokens,
and 25% voice notes, all at once.

**We budget ৳0.15.** It's twice what we expect, it survives a switch to Groq, and Haiku isn't connected
yet, so nothing here is measured on it. Lower it after the pilot if the real number holds.

## 4. Fixed monthly bills

| | Pilot, 10 shops | Launch, 100 shops | Growth, 300 shops |
|---|---|---|---|
| Server | Render 2 GB: $25 | Lightsail 4 GB + snapshots: $26 | Lightsail 8 GB + snapshots: $47 |
| Database | Render 1 GB: $19.30 | Lightsail 2 GB: $30 | Lightsail 4 GB: $60 |
| Qdrant | Cloud free: $0 | On the server: $0 | On the server: $0 |
| Photos (R2) | Free tier: $0 | 30 GB: $0.30 | 90 GB: $1.20 |
| Email (Brevo) | Free: $0 | Free: $0 | Starter: $9 |
| Domains | $1.75 | $1.75 | $1.75 |
| Monitoring, CI | Free tiers: $0 | Free tiers: $0 | Free tiers: $0 |
| **Total** | **$46** | **$58** | **$119** |
| **With the 5% buffer** | **৳6,000** | **৳7,500** | **৳15,400** |

The earlier ৳6,000 for 100 shops was low. It left out snapshots and used a smaller, unencrypted database.

### Why each pick

**Server.** Stay on Render for the pilot, since there's nothing to move. Go to AWS Lightsail in
Singapore at launch: 4 GB is $24 there *(Vendor)* and $85 on Render *(Vendor)*. Render's Hobby plan also
includes only 5 GB of traffic, then $0.15 a GB. Note that `render.yaml` is on the free plan today, which
sleeps and stops the background loops: **real shops need the paid one.**

**Database: AWS Lightsail's managed PostgreSQL from launch, and Render's during the pilot.**

| A month | 100 shops | 300 shops | |
|---|---|---|---|
| **Lightsail managed** | **$30** | **$60** | Cheapest at both sizes; fixed bill; encrypted; next to the server |
| Render | $42 | $64 | Good only while the app is on Render |
| Neon | $34 | $88 | No sizing, smooth growth, but always billing |

Why Lightsail wins *(Vendor)*:
- **It's cheapest as we grow.** Neon is $28 a month more at 300 shops, and that gap keeps widening.
- **Neon's main advantage doesn't apply to us.** Its saving is scaling to zero, but our background loops
  touch the database every 5 to 60 seconds, so it would bill all the time. Its free tier doesn't fit
  either: 100 CU-hours run out in about 16 days at 0.25 CU.
- **It sits beside the server**, so there's one vendor and one bill. It includes PostgreSQL and
  point-in-time restore. I didn't confirm its Singapore region on the page today, so check in the console.
- **Start at the $30 size.** The $15 size has **no encryption**, and we'll hold buyers' names and phones.

The trade-off: growing means making a new, larger database from a snapshot, which takes a short planned
stop. Neon resizes on its own. A standby for high availability doubles the price, so leave that for later.

During the pilot, Render's 1 GB database ($19.30) sits beside the app on Render, so there's nothing to
move until launch. Plan one short move when you go to Lightsail.

**Qdrant: don't pay for it.** 768-dimension vectors need about 4.6 KB of memory each: about 0.5 GB at 100
shops and 1.4 GB at 300, which fits on the server. The Cloud free tier (1 GB memory, 4 GB disk, *Vendor*)
covers the pilot, but it's one node. Its paid clusters start around $75 *(3rd party)*. On our own server,
we take snapshots ourselves.

**Embeddings: keep the Gemini API.** $0.20 per million text tokens and about $0.00012 an image *(Vendor)*,
about ৳0.001 a reply: **about $4 a month at 100 shops**. It's already in the reply cost. A GPU to run our
own model costs $146+ a month *(3rd party)*.

**Email: Brevo, already built in.** The free plan allows 300 emails a day *(3rd party; the page didn't
load)*. Signup codes and notices stay well under that at 100 shops. The next step is about $9 for 5,000 a
month *(3rd party)*. Amazon SES is $0.10 per 1,000 *(3rd party)* if volume ever grows.

**Domains.** A .com is about $11.50 a year *(3rd party; the wholesale price rose on 1 Nov)*. A .com.bd is
৳700 to register and ৳1,020 to renew plus VAT, at BTCL's January 2026 rate, which was described as
time-limited *(3rd party)*.

## 5. Speech to text

| | Price | A 15-second note | Bangla |
|---|---|---|---|
| **Soniox, files** | $0.10 an hour *(Vendor)* | ৳0.051 | Bengali is on its list *(Vendor)*; not tested by us |
| Soniox, live | $0.12 an hour *(Vendor)* | n/a; ৳0.25 a minute | Same |
| Groq Whisper turbo | $0.04 an hour, 10-second minimum *(3rd party)* | ৳0.021 | Already built |

You're right that Soniox is cheap: even at 2.5× Whisper, it's half a paisa on an average reply. **So
choose on Bangla accuracy, not price.** Test both on 50 real voice notes. Soniox needs a small new
adapter (`STT_PROVIDER` has no Soniox option yet).

## 6. Bangla text to speech

Voice is switched off today, so **this isn't in the plan costs**. One minute of speech is about 830
characters. A spoken reply of 200 characters costs:

| | Price | A minute | A reply | Notes |
|---|---|---|---|---|
| Google Standard | $4/million *(from memory; page didn't load)* | ৳0.41 | ৳0.10 | Cheapest; older robotic style; I didn't confirm a Bangla voice |
| Sarvam Bulbul v4 Flash | ₹1/1,000 *(Vendor; ₹ rate assumed)* | ৳1.13 | ৳0.27 | Indian service; Bengali not confirmed |
| **Soniox** | $0.70 an hour *(Vendor)* | **৳1.43** | **৳0.34** | One vendor with STT; Bengali on its list, voice untested |
| **Azure Neural** | $15/million *(3rd party)* | **৳1.54** | **৳0.37** | **Real Bangladeshi voices** `bn-BD-NabanitaNeural` and `bn-BD-PradeepNeural` *(Vendor)*; 0.5M characters free monthly, about 600 minutes *(unconfirmed)* |
| Gemini 2.5 Flash TTS | $10/million audio tokens *(Vendor; 1,500 tokens a minute assumed)* | ৳1.86 | ৳0.45 | `bn-BD` listed as GA *(Vendor)* |
| Google Chirp 3 HD | $30/million *(from memory)* | ৳3.08 | ৳0.74 | |
| ElevenLabs Flash | $60/million *(3rd party)* | ৳6.15 | ৳1.48 | Too expensive for this |
| Our own `tts_server` on a GPU | $146+ a month *(3rd party)* | Fixed | Fixed | Beats Azure only above about 11,700 minutes a month |

**Pick: test Azure and Soniox with 20 Bangla sentences and choose by ear.** They cost about the same.
Azure is the safer guess because it has real Bangladeshi voices and a free tier. Google Standard is the
cheapest, but its price and Bangla voice are both unconfirmed.

**When voice comes back:** count one spoken reply as **3 replies**. It costs about ৳0.44 against ৳0.83 on
Starter (47% margin) as one reply, or ৳2.49 (82%) as three. A two-minute AI call is about ৳2.30 in
speech and model costs, plus a carrier I couldn't price, so sell calls as an add-on.

## 7. Pricing: what changes and why

| Plan | Price | Replies | Per reply | Expected ৳0.08 | **Budget ৳0.15** | Worst ৳0.25 | Half used, budget |
|---|---|---|---|---|---|---|---|
| Starter | ৳2,490 | 3,000 | ৳0.83 | 88% | **79%** | 67% | 88% |
| Growth | ৳5,990 | 12,000 | ৳0.50 | 81% | **67%** | 47% | 82% |
| Pro, today | ৳12,990 | 35,000 | ৳0.37 | 76% | 57% | 30% | 77% |
| **Pro, new** | ৳12,990 | **30,000** | **৳0.43** | 79% | **63%** | **40%** | 80% |

Margins are after AI and the 2.5% payment fee, with every reply used unless stated.

**Keep the prices:**
- **Cost doesn't force a rise.** Every plan makes money even in the worst case, with every reply used.
- **They beat a person.** Job ads put a page moderator at about ৳8,000 to ৳20,000 a month *(3rd party,
  undated ads)*. Starter costs about a fifth of a ৳12,000 moderator and works all night. Pro costs
  about one salary and covers all three shifts.
- **A cut has little upside until we have evidence.** We have no paying shops yet, so the pilot should
  test what they'll pay. Raising a price later is harder than holding one.
- **The free trial stays.** It costs only about ৳20 to ৳30 per shop.

**Lower Pro to 30,000 replies:**
- It's the only plan under 60% at budget, and at 30% in the worst case. Since heavy shops are the most
  likely to use everything, that's where it matters.
- The change lifts it to 63%, and 40% in the worst case, at no cost to any customer: nobody is on Pro yet.
- 30,000 is still 1,000 replies a day, which very few shops will reach.

**Not changing now:**
- **A cheaper entry plan.** About ৳990 for 800 replies would make an 85% margin and answer ChatBD's
  ৳999, but each small shop also brings support work. Decide after the pilot shows how much.
- **Extra replies for sale.** About ৳0.50 a reply would earn about 68%, and stop the agent going quiet during
  Eid. It's the best next pricing feature, but it needs building.

**The risk to prepare for:** your draft has Sellzy at ৳0.16 a message. If that's true, Starter costs 5
times more per reply at full use. We can't beat it on price: our budgeted cost is ৳0.15. The answer is
that we sell correct orders, the read-back and a yes, plus the team inbox. LazyChat's prices are confirmed
from its site on 4 Nov 2026 (৳4,999, ৳12,500, ৳26,000). **Re-check the others before presenting.**

## 8. The business

Mix: 60% Starter, 30% Growth, 10% Pro, using half their replies. 20% pay yearly (2 months free). 5% of
shops leave each month, and 10% of trials convert (assumed: sellers are wary of new tech).

| At budget ৳0.15 | |
|---|---|
| List price, on average | ৳4,590 |
| Collected, after yearly discounts | ৳4,437 |
| Average replies | 4,200 |
| **Left from each shop, after AI and fees** | **৳3,696** |

| Shops | Money in | Team (assumed) | Fixed | **Profit a month** |
|---|---|---|---|---|
| 50 | ৳2.2 lakh | ৳1.2 lakh | ৳7,500 | **-৳1.4 lakh** |
| 89 | ৳3.9 lakh | ৳1.2 lakh | ৳7,500 | **Break-even** |
| 100 | ৳4.4 lakh | ৳1.2 lakh | ৳7,500 | **৳42,000** |
| 300 | ৳13.3 lakh | ৳1.7 lakh | ৳15,400 | **৳5.7 lakh** |

After marketing of ৳2 lakh a month, or ৳3.5 lakh at 300 shops.

**Marketing, not the AI, decides profit.** All of it is assumed until the pilot measures it:

| ৳2 lakh a month | | |
|---|---|---|
| Ads, seller groups, partners | ৳1,31,100 | about ৳1,000 for each of 130 trials |
| Hands-on setup | ৳65,000 | about ৳5,000 for each of 13 paying shops |
| AI for free trials | ৳3,900 | 200 replies at ৳0.15, ৳30 a trial |

A paying shop costs about ৳15,400 and pays that back in 4.2 months. Facebook lead costs here are not
published; the closest guide is India at ₹150 to ₹2,000 a lead *(3rd party, agency estimate)*, so run a
small test first.

| Each paying shop costs | Money needed | Break-even, after launch |
|---|---|---|
| ৳15,400 | ৳14.6 lakh | 9 months |
| 1.5x | ৳22.3 lakh | 14 months |
| Double | ৳31.9 lakh | 23 months |

Each includes a 3-month free pilot. These are the numbers most worth replacing with real ones.

Not included: office, company registration and trade licence, income tax, and any SSLCommerz
or bKash setup fee. 15% VAT is on top of our prices, paid by the shop. WhatsApp's charge of about ৳0.46 a
message after 1,000 free *(3rd party)* is also paid by the shop, and can reach ৳920 on a 3,000-reply
plan: **say so on the pricing page.**

## 9. What to measure or confirm

Assumed by me, and most likely to move the numbers:
1. **Tokens on Claude**: 1.3× our trace counts. Run `count_tokens` once the adapter exists.
2. **Output**: 400 tokens with thinking on, against 115 measured without it.
3. **The stable share of the prompt**: 85%.
4. **Voice notes**: 10% of messages at 15 seconds.
5. **Team cost, marketing cost, churn, trial conversion and yearly share.** All placeholders.

Also: our traces are only 120 turns, mostly from one test shop, and not real buyers. The pilot's numbers
replace all of this. Before relying on it:
- Haiku 5.5's Bangla and Banglish quality on our own chats.
- Maira's price, from Gigalogy.
- Our real SSLCommerz and bKash rates (2.5% is *3rd party*).
- Every competitor's current price, except LazyChat's.

## Sources

- Claude Haiku 5.5 prices and cache rates: Anthropic's API reference, https://platform.claude.com/docs/en/about-claude/pricing
- Soniox: https://soniox.com/pricing · languages: https://soniox.com/speech-to-text/use-cases/voice-agents
- Neon: https://neon.com/pricing · regions: https://neon.com/docs/introduction/regions
- AWS Lightsail: https://aws.amazon.com/lightsail/pricing/ · databases: https://docs.aws.amazon.com/lightsail/latest/userguide/amazon-lightsail-databases.html
- Render: https://render.com/pricing.md
- Qdrant: https://qdrant.tech/pricing/ · paid estimate (3rd party): https://leanopstech.com/blog/qdrant-cloud-pricing-2026/
- Gemini embeddings, TTS and Flash-Lite: https://aistudio.google.com/docs/pricing · bn-BD: https://docs.cloud.google.com/text-to-speech/docs/gemini-tts
- Cloudflare R2: https://developers.cloudflare.com/r2/pricing/
- Azure voices: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts · price (3rd party): https://docs.litellm.ai/docs/providers/azure_ai_speech
- Sarvam: https://docs.sarvam.ai/api/getting-started/pricing.md
- ElevenLabs (3rd party): https://www.camb.ai/blog-post/text-to-speech-price-comparison
- Brevo (3rd party): https://dev.to/techsifted/brevo-pricing-2026-every-plan-explained-with-the-hidden-costs-4oe3 · SES: https://aws.amazon.com/ses/pricing/
- Domains (3rd party): https://costgoat.com/compare/domains · https://www.bssnews.net/news/350126
- Moderator pay (job ads): https://bikroy.com/bn/ads/q/bangladesh/online-jobs
- Groq, Whisper, GPU, WhatsApp and payment fees: carried over from the 2 Nov research, not re-checked today. Groq: https://groq.com/pricing · RunPod: https://computeprices.com/providers/runpod/gpus/rtx4000ada · WhatsApp: https://www.ycloud.com/blog/whatsapp-api-message-pricing-update-effective-october-1-2026 · fees: https://inai.io/blog/top-10-payment-gateways-in-bangladesh
