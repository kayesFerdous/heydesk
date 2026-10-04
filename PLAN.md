# HeyDesk — the plan

An MVP in about four weeks: a business's AI front desk you can **talk to**. How
it works is in [docs/design.md](docs/design.md); decisions in
[docs/decisions.md](docs/decisions.md).

> **Now:** days 1–23 built, then the product made simpler and more real (4 Oct, D22–D26: one agent per business, owner and admin roles, one records inbox, owner-friendly setup). Demo rehearsals and the UI wait. **Data layer done (D27–D30):** item attributes, conditions in `search_items`, CSV import, the clinic preset. **Next:** a live check with a real model on the clinic, then real bookings or the shop preset.
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

## Next: what the agent can do, and a knowledge base (RAG)

Decided 4 Oct: Gemini embeddings, text/Markdown/PDF documents, a shared jobs library. One commit per step.

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
