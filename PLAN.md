# HeyDesk — the plan

An MVP in about four weeks: a business's AI front desk you can **talk to**. How
it works is in [docs/design.md](docs/design.md); decisions in
[docs/decisions.md](docs/decisions.md).

> **Now:** days 7 and 8 done (2 Oct), checked with real Jev on Groq and Gigalogy. **Next:** day 9, tests and catch up.
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

**Done when:** the scripted order passes offline and `scripts/check.sh` is green. [ ]

## M2 — Voice

**Day 10 — Voice spike**
- Try Pipecat (SmallWebRTC) first, LiveKit Agents if it fights you; STT + TTS (e.g. Deepgram + Cartesia)
- An echo call in the browser; measure first-sound time
- Log the choice in decisions.md

**Done when:** you talk, it answers out loud, and the decision is written down. [ ]

**Day 11 — Order by voice**
- `voice/sentences.py`: speak sentence by sentence
- `voice/pipeline.py`: final transcript → `start_turn(channel="voice")` → TTS
- A **Call** button on the dev page

**Done when:** one order placed by voice. [ ]

**Day 12 — Spoken forms**
- Money, time, phone and order number in words (`runtime/speech.py`)
- Used on voice by the state note and tool results; `speakable()` before TTS

**Done when:** the total and the read-back are heard as words. [ ]

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

**Done when:** an order placed on the page shows up in the API with its conversation. [ ]

**Day 20 — Login**
- `users`, signup and login (pwdlib + pyjwt); `current_workspace` from the token
- `heydesk create-user` for the dev workspace

**Done when:** a second account can't see the first account's agent (404). [ ]

**Days 21–23 — Tidy up**
- Isolation test per resource; one error shape everywhere; `MAX_TURNS`
- README: how to run, switch presets, use the API; catch up

**Done when:** `scripts/check.sh` is green and a fresh clone runs from the README. [ ]

## Demo (days 24–27) and buffer (28–30)

- Demo data in the seeds; a reset script; a five-minute demo script (web order, voice order, interruption, hand-off, edit the agent)
- Rehearse twice; record a backup video. Deploying is optional: browsers allow the microphone on `localhost`

**Done when:** two clean rehearsals in a row and a backup video. [ ]

**If you fall behind, cut in this order:** login (day 20) → the receptionist
preset (day 17) → barge-in (day 14). Never cut: the commit check, voice, and the trace.

## Open questions

| # | Question | Needed by |
|---|---|---|
| Q1 | ~~Groq API key~~ and ~~Gigalogy keys~~ in `.env`: both work (Gigalogy's 401 on 1 Oct was on their side and went away) | Day 4 |
| Q2 | ~~TypeSafe (Jev) API key~~ in `.env` (Frontdesk's), `DECIDER=jev` | Day 7 |
| Q3 | Voice framework: Pipecat or LiveKit Agents | Day 10 |
| Q4 | Speech-to-text and text-to-speech providers, and a budget | Day 10 |
