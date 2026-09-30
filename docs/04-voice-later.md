# 04 — Voice, later (v3)

Voice comes after the MVP ships. This doc lists **what to keep true now** so
voice is a new channel rather than a rewrite, and **what adding it involves**.

The goal: **voice = a `voice/` package (or process) that turns audio into
`handle_turn()` calls and tokens back into speech. No change to `runtime/`,
`tools/`, `presets/` or the tables beyond a couple of small additions.**

Going async (v3) makes this easier than v2 planned. `handle_turn()` is already
an async generator of events, which is exactly what a voice framework wants to
consume. No threads, no bridge.

---

## 1. Keep these true now

| Rule | Where | Why voice needs it |
|---|---|---|
| `handle_turn()` is the one entry point, and it **streams** events | `services/conversations.py` | Voice speaks while the model is still writing |
| `runtime/` never imports FastAPI, SQLAlchemy or an SDK | `scripts/check_imports.sh` | A voice worker reuses it unchanged |
| Channel-specific wording lives in the **context note**, not the system prompt | `runtime/context.py` | One published prompt serves web and voice; the cache holds |
| Conversations record `channel` and `caller_ref` | `models/conversation.py` | Caller ID, and filtering calls in the dashboard |
| Tools declare an optional `filler` | `tools/base.py` | Something to say while a slow tool runs |
| Tools return resolved values **in words** ("Friday 3 October", "৳1,250") | every tool | They'll be read aloud as-is |
| Transcripts are append-only | rule 5 in 02 | Barge-in must be recorded by *adding*, not editing (§3) |
| Jev guard runs in parallel; `reasoning_effort` `low`; prompt and tools first so Groq caches them | `runtime/loop.py`, the adapter | Every saved millisecond is dead air saved |
| SDK clients are built once in the lifespan | `main.py` | No cold TLS handshake on the first word of a call |
| The trace has an empty `marks` slot | `runtime/trace.py` | Voice timings go there |

Nothing else about voice should shape the code before voice exists.

---

## 2. What voice adds, and who solves it

| Problem | Solved by |
|---|---|
| Audio in and out of a phone line | A telephony provider (e.g. Twilio) streaming audio over a WebSocket, or WebRTC |
| Speech → text, text → speech | Speech providers, through a voice framework |
| Knowing when the caller has finished speaking | The framework's voice-activity and turn detection |
| The caller interrupting (barge-in) | The framework stops playback; **you** record what was heard (§3) |
| Silence while a tool runs | **You**: say the tool's `filler` if it's expected to take more than ~0.5 s |
| Misheard names and numbers | **You**: voice style in the context note ("spell names back letter by letter before placing anything"); the commit check still applies |
| Knowing where time goes | **You**: trace marks |

**Use a framework for the audio plumbing.** Pipecat and LiveKit Agents are the
usual open-source choices; both are async Python. Frontdesk's own plan warns
that writing your own audio transport proves nothing and costs weeks. The
brain is the part you've built.

---

## 3. The shape of `voice/`

```
src/heydesk/voice/
├── app.py        entry point: a FastAPI WebSocket route for the telephony stream, or a framework worker
├── pipeline.py   framework glue: final transcript → handle_turn() → sentences → speech
├── sentences.py  cut the token stream into sentences, so speech starts after the first one
└── routing.py    dialled number → agent; caller ID → caller_ref
```

The glue, in outline:

```python
async def on_final_transcript(call: Call, text: str, heard: float):
    events = services.conversations.handle_turn(
        call.agent, call.conversation_id, text, channel="voice", caller_ref=call.caller_number,
    )
    async for sentence in sentences(events, on_tool=lambda tool: call.say(tool.filler)):
        await call.speak(sentence)          # the framework's TTS call
```

Because it's all async, this can run inside the same FastAPI app as a
WebSocket endpoint while you're small, and move to its own process when calls
need their own scaling.

**Small additions the rest of the code will need:**

| Addition | Where | Why |
|---|---|---|
| `agents.voice_number` (unique, nullable) | `models/agent.py` + migration | A call to this number reaches this agent |
| A voice style block | `runtime/context.py` | "No lists or markdown. Say prices and times as words. Keep replies to one or two sentences. Spell names back." |
| Barge-in note | `services/conversations.py` | See below |
| Caller-ID detail | `runtime/tasks.py` + the state note | The caller's number pre-fills every task's `phone` detail as *unconfirmed*; the state note says "confirm the number ending 678 instead of asking for it". One yes instead of eleven spoken digits. |
| Voice marks in the trace | `runtime/trace.py` + the glue | speech ended → transcript final → first model token → first audio out |

**Barge-in, append-only.** The caller cuts in after the first of three offered
times. Don't edit the saved assistant message down to what they heard.
Editing earlier history breaks Groq's prefix cache and makes the transcript
disagree with the trace. Instead, the next turn starts with a
context note:

> The caller interrupted your last reply. They heard only: "We have 7 pm free…"

The full generated reply stays in the trace; the dashboard shows both. This is
Frontdesk's §4.3 fix, adapted to append-only transcripts.

---

## 4. The latency budget

What Frontdesk measured on a live call, and what the v3 design changes:

| Stage | Frontdesk (measured) | HeyDesk |
|---|---|---|
| Waiting to be sure the caller stopped | ~700 ms | The framework's setting; tune it |
| Judgment + tools + model to first token | **1.2–3.0 s**: the model round trip dominated | Guard in parallel (≈0 ms added), cached prompt + tools, `reasoning_effort` `low`, Groq's fast inference, first sentence spoken as soon as it's complete |
| Speech rendering | 3–19 ms | Similar |
| Last word → first sound (P50) | **2.2 s**, against a target of ~0.8 s | **Measure first**; write it in the README |

Frontdesk's lesson: *"the voice stack is not the problem; the round trip to
the model is."* The biggest levers in v3 are the cached prefix, `low` effort,
speaking sentence by sentence, and fillers on tools. If that's still too slow,
the next step is a faster model *for voice agents only* (e.g.
`openai/gpt-oss-20b`). It's a per-agent
setting, so that's a config change, not a code change.

---

## 5. When you start voice: the order

1. Buy a number from a telephony provider; add `voice_number` to one agent.
2. Framework hello-world: a call that repeats what you said.
3. Wire `pipeline.py` to `handle_turn()`. **Place one order by phone.**
4. Voice style + caller-ID in the context note; sentence streaming.
5. Trace marks; measure last-word → first-sound. Write it down.
6. Barge-in note. Test: interrupt a list of three times after the first.
7. Tool fillers.
8. Run every scenario with `channel="voice"` (text in, text out). They should
   pass unchanged; if not, something voice-shaped leaked into the core.
