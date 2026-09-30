# 01 — What Frontdesk is, and what HeyDesk should take from it

This is a read of `../Frontdesk`. The goal is to decide what to copy into
HeyDesk, what to make smaller, and what to leave out.

Read next: [02-build-guide.md](02-build-guide.md), [03-project-structure.md](03-project-structure.md),
[04-voice-later.md](04-voice-later.md), [05-one-month-plan.md](05-one-month-plan.md) and
[06-production-and-scaling.md](06-production-and-scaling.md).

---

## 1. Frontdesk in one paragraph

Frontdesk is a booking agent for a doctor's clinic. A patient chats (on the web,
or on a simulated phone call) and the agent collects what it needs — appointment
type, doctor, day, time, name, phone — reads the details back, books the slot,
and sends a payment link if the visit costs money. It is built with **FastAPI,
async Python and raw SQLite**, about **13,500 lines**, **343 tests** and **38
end-to-end scenarios**, built in seven phases.

## 2. How one message flows through it

```
patient types a message
        │
        ▼
channels/web/chat.py          POST /chat  (streams back over SSE)
        │
        ▼
core/loop.py::run_turn()
   1. Decider.decide(...)      the model CLASSIFIES: intent? wants a human?
                               did they give their name? which weekday?
   2. policy.step(...)         PYTHON decides what happens next:
                               ask / offer times / confirm / book / escalate
   3. tools.run(...)           read availability, or write the booking
   4. LanguageModel.stream()   the model PHRASES one sentence
   5. save conversation + a per-turn trace
        │
        ▼
the reply streams back; /review/{id} shows timings, judgments, tool calls
```

## 3. Its layers

| Folder | What it holds |
|---|---|
| `domain/` | `Appointment` and `Conversation` classes plus value objects (`PhoneNumber`, `Money`, `TimeSlot`). Pure Python, imports nothing else. |
| `core/` | The agent: `loop.py` (a turn), `policy.py` (the state machine), `entities.py` (dates), `tools.py`, `trace.py`, `jobs.py` (background worker). No I/O, no framework. |
| `ports/` | `Protocol` interfaces for everything outside: `Decider`, `LanguageModel`, `ClinicSystem`, `PaymentProvider`, `Notifier`, `JobQueue`, repositories. |
| `adapters/` | Implementations: SQLite, in-memory, Groq/Gemini/Anthropic/Gigalogy, the Jev decider, fakes, a stub payment provider, voice detectors. |
| `packs/clinic/` | Everything clinic-specific: slots, tools, prompts (one `.txt` per state), rules (fees, hold time), `seed.json`. |
| `channels/` | Ways in: a terminal REPL, the web app, a voice call simulator. |
| `config.py` | The one place that picks adapters and wires everything together. |
| `evals/` | 38 scripted conversations, a grader, and a recorded "cassette" so CI replays them for free. |

A shell script (`scripts/check_deps.sh`) greps the imports to enforce three
rules: `domain/` imports nothing; `domain/` and `core/` never import an adapter
or a framework; `core/` never imports the clinic pack directly.

## 4. The ideas that make it good

These are the reasons Frontdesk works. They're all cheap, and every one of
them survives into HeyDesk, though not always in the same form. Idea 1 changes
the most: the guarantees move from a state machine into the tools. §6 says how
each one carries over.

1. **The policy is code, not a prompt.** Which question comes next, when to read
   back, when to book — all in `core/policy.py`. The model only understands the
   message and writes the sentence. It never chooses a branch. That is what makes
   the agent testable: you can assert "after this message, the state is
   `confirming`".

2. **The model reads parts; Python does the maths.** A date comes back as parts
   (`weekday=tuesday`, `next_week=true`) and Python turns it into a real date.
   Models get "next Tuesday" wrong often enough to matter.

3. **Read back before booking.** Critical details are confirmed by the patient
   before any write happens.

4. **The database stops double bookings, not your code.** Three unique
   constraints: one active booking per doctor + start time, one booking per
   idempotency key, one confirmation per payment event. A race loses to the
   database.

5. **A write is never served from a cache.** The booking re-checks availability
   instead of trusting a list fetched a few seconds ago.

6. **The appointment's status only changes through its own methods** —
   `hold()`, `confirm_payment()`, `expire()`, `cancel()`. `confirm_payment()` is
   idempotent, so a webhook sent twice is harmless.

7. **"Today" is the clinic's today.** Timestamps are stored in UTC, but
   "tomorrow" means tomorrow in the clinic's timezone (Asia/Dhaka). Between
   midnight and 6am the UTC date is still yesterday there.

8. **Every turn leaves a trace** — what the model judged, what Python decided,
   which tools ran and how long each step took — *including the turns that
   failed*. A failed provider becomes a hand-off to a human, and the turn is
   still saved.

9. **A fake for every provider.** `FakeModel` and `FakeDecider` make tests free,
   fast and offline, and let you run the app with no API key.

10. **A clearing rule.** If the patient changes the doctor or the day, the time
    they picked is cleared too, because the time depended on those. Frontdesk
    found this bug in a live run: it read back Wednesday and booked Tuesday.

11. **Empty model output is an error, not "the patient said nothing".** A
    reasoning model that ran out of tokens returns an empty string. Treating that
    as "no answer" silently lies to the policy.

## 5. What makes it complex

Most of Frontdesk's size comes from voice and from measuring things. It was
built to impress a voice-agent company, so it optimises latency and compares
providers in ways HeyDesk won't need until much later, if ever. Several
items below (FastAPI, Jev, packs-as-presets, streaming, voice-readiness) do
come back in HeyDesk, in a smaller form. See §6.

| Complexity | Why it exists in Frontdesk |
|---|---|
| Voice channel: endpointing, barge-in, filler phrases, audio cache, speculative prefetch | Phone calls. About a third of the hard code. |
| Two kinds of model (`Decider` for judgments, `LanguageModel` for text) plus Jev, shadow deciders, `ComparingDecider` | Comparing providers with agreement rates. |
| Confidence scores and per-question thresholds, spell-back | Speech recognition mishears names. |
| Domain "packs" loaded by name | Proving a second business could reuse the core. |
| Pure `domain/` objects + repository Protocols + SQLite and in-memory repositories | Keeping `core/` totally free of storage. Costs a mapping layer. |
| Async everywhere + raw SQL with `aiosqlite` | FastAPI and streaming voice. |
| Its own durable job queue with leases, backoff and reconciliation | Payment links must survive a restart. |
| Tiered tool cache, prompt-prefix caching, cost per turn | Latency and bill measurements. |
| English + Bangla word lists | Callers mix languages. |
| Recorded cassettes for evals | Running 38 live scenarios costs a day of free-tier quota. |

## 6. Keep / Simplify / Drop

*Revised (v3).* HeyDesk is now a **SaaS where businesses build their own
agent** from a preset and edit its instructions, knowledge and tools. That
changes one big thing: the conversation is driven by the language model
through tools, not by a hard-coded state machine, because customers must be
able to reshape it. Frontdesk's guarantees move **into the tools**, where
customers can't edit them away.

| Frontdesk | HeyDesk v3 | Why |
|---|---|---|
| Policy as code: a state machine decides every step | **Kept as data**: presets define **tasks** and their **details**; code tracks what's missing and picks the next step (`next_step`), and a **state note** tells the model before every call. The model writes the words; **code guarantees** prices, totals, availability, validation and commits | Customers edit the task lists and wording; a fixed state machine in code can't be edited by them |
| "Nothing is booked without a read-back" | **Keep, enforced in code**: every writing tool runs a Jev commit check | The one step that can't be undone shouldn't rely on the prompt |
| `Decider` port + typed questions | **Keep**: Jev for the hand-off guard and the commit check; conversation labels later | Cheap, fast, typed; one clear job each |
| `JevDecider` / `LlmDecider` / `FakeDecider` | **Jev + fake now**, `LlmDecider` later (Frontdesk's Jev adapter is already async) | Swappable provider; free tests |
| `LanguageModel` port | **Keep, now central**: streaming + tool calls; Groq (`openai/gpt-oss-120b`) by default | It's the conversational engine now |
| Clearing rule (change the day → the time is cleared) | **Keep, as data**: `depends_on` on a detail | Same bug, same fix, no code per preset |
| Date parts resolved in Python | **Adapted**: a two-week calendar in the context note + tools validate dates and reply with weekdays in words | The model looks dates up instead of computing them; code still checks |
| Model never does arithmetic | **Keep**: totals computed in `update_order` | Money must be exact |
| Domain packs | **Become presets** (YAML + a knowledge model) + a shared **tool library** | A preset is a starting point customers copy and edit |
| Business facts in `seed.json` | **Agent knowledge** per workspace, validated by the preset's model; `.env` seed file in dev | Many customers |
| Channel-agnostic `run_turn()` | **Keep**, as an async generator of events | Web now, voice later, same core |
| FastAPI + async + SSE | **Keep** (v1/v2 had switched to Flask; v3 switches back) | Streaming, many concurrent chats, voice later |
| Prompt-prefix caching | **Keep, by design**: prompt + tools frozen per agent version | Lower cost and time to first token on every turn after the first |
| Per-turn trace + review page | **Keep** + token usage and cost per turn | Debugging and, later, billing |
| Three unique constraints | **Keep** for bookings; idempotency keys for orders and bookings | Double-booking and double-order protection in the database |
| Repository Protocols | **Narrowed**: `ports/stores.py` for what tools may ask of the database; the SQLAlchemy session everywhere else | Keeps the import direction right where it matters, without a mapping layer |
| Value objects, `Confirmed[T]` | **Keep** in `domain/values.py` | Writing an unconfirmed order becomes a type error |
| Custom durable job queue | **Later, same design on Postgres** (leases, backoff, `SKIP LOCKED`); nothing runs in the background in the MVP (06) | Nothing must survive a restart yet |
| Payments | **Later** | Not in the first month |
| Voice (endpointing, barge-in, fillers) | **Designed for, built later**: see [04-voice-later.md](04-voice-later.md) | Barge-in becomes an appended note, because transcripts are append-only |
| Tool cache tiered by role, in-flight joins, shadow/comparing deciders, speculative prefetch, audio cache | **Later**, each with a trigger; `Tool.role` exists from day one so the cache rule has something to read (06) | Measurement and latency tricks for later |
| Bangla word lists | **Later** | English first |
| Scenario evals + cassettes | **Keep the idea now**: scenario YAML per preset, played with `FakeLLM` or `-m live`; the runner with cassettes in the production phase reads the same files (06) | Check outcomes (rows written), not wording |
| `check_deps.sh` | **Keep**: `check_imports.sh` | A rule nobody checks is a comment |

## 7. Lessons from Frontdesk's bug list

Frontdesk's README records the bugs its scenario tests found. They are worth
knowing before you write any code, because each one is easy to repeat.

- **The model never saw what the user just said.** The turn was saved after the
  reply, so the reply was built from *previous* turns only. Always pass the
  current message in.
- **The opening message was read for intent only.** "Book a consultation with Dr
  Rahman on Tuesday at 2" contains the whole booking. Extract every field on
  every turn, including the first.
- **A retried booking lost to itself.** The idempotency key was stored but never
  looked up, so a double-click hit "slot taken". Look up the key first.
- **"Eleven o'clock" parsed to nothing.** Handle times in words, not only digits.
- **A time said too early was forgotten.** "Thursday at 11" arrives before the
  doctor is known. Keep the wanted time and use it once availability is known.
- **Asking on presence vs. accepting on validity.** If the "is this slot filled?"
  check and the "what do I ask next?" check disagree, the agent asks the same
  question forever. Use one function for both.
- **"I'd like to see someone about a rash" read as "give me a human".** Word the
  hand-off question carefully: wanting a doctor is not wanting a receptionist.
