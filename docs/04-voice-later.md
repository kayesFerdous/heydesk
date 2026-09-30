# 04 — Voice (v3.1): the channel, built in week 4

Voice was planned for after the MVP. In v3.1 (PLAN.md, D1) **the voice-ready
core is built in M1–M2 and the voice channel in M4** (days 22–27): a browser
call first, a phone number as a stretch. This doc lists **what the core
keeps true** so voice is a new channel rather than a rewrite, and **what the
channel adds**. How the conversation should *feel* on voice (fillers,
interruptions, spoken numbers, turn-taking, silence) is specified in
[07-conversation-experience.md](07-conversation-experience.md).

The goal: **voice = a `voice/` package that turns speech into `start_turn()`
calls and the turn's events back into speech. No change to `runtime/`,
`tools/`, `presets/` or the tables beyond a couple of small additions.**

Going async (v3) makes this easy: a running turn is already an async stream of
events, which is exactly what a voice framework wants to consume. No threads,
no bridge.

---

## 1. Keep these true in the core (M1–M2)

| Rule | Where | Why voice needs it |
|---|---|---|
| `start_turn()` is the one entry point; the turn **streams** events, runs in its own task, can be **interrupted** by a signal, and is told what was **heard** | `services/conversations.py` (02 §10) | Voice speaks while the model is still writing, and must stop mid-sentence without losing the turn |
| `runtime/` never imports FastAPI, SQLAlchemy or an SDK | `scripts/check_imports.sh` | The voice channel reuses it unchanged |
| Channel-specific wording lives in the **context note**, not the system prompt | `runtime/context.py` | One published prompt serves web and voice; the cache holds |
| Conversations record `channel` and `caller_ref` | `models/conversation.py` | Caller ID, and filtering calls in the dashboard |
| Tools declare `label`, `fillers`, `expected_ms`, `timeout_s`; the runtime's **progress policy** emits `Progress` events | `tools/base.py`, `runtime/progress.py` (07 §3) | Something to say while a slow tool or a slow model runs. Web shows the label from M1; voice speaks the text in M4 |
| Every value has a **spoken form**, and the state note and tool results use it on voice | `runtime/speech.py` (07 §5) | Read-backs must be exact words, not digits the model converts |
| The commit check reads the **heard** text | `runtime/guards.py` (02 §6) | A read-back cut off by a barge-in isn't a read-back |
| The state note exposes `expects` (the next detail's kind) | `runtime/state_note.py` | The channel waits longer while a phone number is read out (07 §7) |
| Transcripts are append-only | rule 5 in 02 | Barge-in is recorded by *adding* a note, not editing (§3) |
| Jev guard runs in parallel with a deadline; `reasoning_effort` `low`; prompt and tools first so Groq caches them | `runtime/loop.py`, the adapter | Every saved millisecond is dead air saved |
| SDK clients are built once in the lifespan | `main.py` | No cold TLS handshake on the first word of a call |
| The trace has a `marks` slot | `runtime/trace.py` | Voice timings go there |

Nothing else about voice should shape the core.

---

## 2. What voice adds, and who solves it

| Problem | Solved by |
|---|---|
| Audio in and out (browser first, then a phone line) | The framework's transport: WebRTC for the browser; SIP or a telephony provider's media stream for phones |
| Speech → text, text → speech | Speech providers, through the framework |
| Knowing when the caller has finished speaking | The framework's semantic turn detection, tuned; **we** lengthen it when `expects` is a phone number (07 §7) |
| The caller interrupting (barge-in) | The framework stops playback; **we** signal the turn and record what was heard (§3) |
| Backchannels ("mm-hmm") treated as interruptions | Framework settings: minimum words or duration before a barge-in counts (07 §7) |
| Silence while a tool or the model runs | **Us**: the progress policy's lines (07 §3). The channel drops a line if audio is still playing or the caller is talking |
| Misheard names and numbers | **Us**: keyword boosting from knowledge, spoken-digit parsing, spell-back, caller-ID pre-fill (07 §6). The commit check still applies |
| Caller goes quiet, call should end | **Us**: scripted reprompt and goodbye, the `end_call` tool (07 §8) |
| Knowing where time goes | **Us**: trace marks |

**Use a framework for the audio plumbing.** LiveKit Agents and Pipecat are
the usual open-source choices; both are async Python, and both let you
replace the "LLM step" with your own code, which is the one thing we need.
The day-22 spike picks one (PLAN.md Q3). Frontdesk's own plan warns that
writing your own audio transport proves nothing and costs weeks. The brain is
the part you've built.

---

## 3. The shape of `voice/`

```
src/heydesk/voice/
├── app.py         entry point: the framework worker (a WebRTC room per call), or a WebSocket route
├── pipeline.py    framework glue: final transcript → start_turn() → sentences → speech; interrupt; heard text
├── sentences.py   cut the token stream into sentences, so speech starts after the first one
├── speakable.py   last-line normaliser before TTS: no markdown, symbols expanded (07 §5)
└── routing.py     room / dialled number → agent; caller ID → caller_ref
```

The glue, in outline:

```python
async def on_final_transcript(call: Call, text: str) -> None:
    if call.turn and not call.turn.done:              # they spoke again before we finished
        await interrupt(call)
    call.turn = await services.conversations.start_turn(
        call.agent, call.conversation_id, text, client_message_id=uuid4().hex,
        channel="voice", caller_ref=call.caller_number,
    )
    async for sentence in sentences(call.turn.events(), on_progress=call.say_if_quiet):
        await call.speak(sentence)                    # the framework's TTS; tracks which spans finished playing
    call.turn.delivered(call.heard_text())            # what the caller actually heard → saved with the turn

async def interrupt(call: Call) -> None:              # also the framework's barge-in callback
    call.stop_audio()                                 # ~200 ms to silence
    call.turn.interrupt()                             # the turn stops talking at the next token
    call.turn.delivered(call.heard_text())
    await call.turn.saved()                           # the lease is released; the next turn can start
```

It can run inside the same FastAPI process while you're small, and move to
its own worker process when calls need their own scaling (06 §7).

**Small additions the rest of the code will need:**

| Addition | Where | Why |
|---|---|---|
| `agents.voice_number` (unique, nullable) | `models/agent.py` + migration | A call to this number reaches this agent (phone stretch) |
| `AgentConfig.voice`: TTS voice, filler phrases, disclosure line, silence timings, reprompt and goodbye lines | `runtime/config.py` | Per-agent voice settings; phrases validated like other scripted lines (07 §3) |
| The voice style block | `runtime/context.py` | "One or two sentences. No lists or markdown. At most three options. Say numbers the way the state note gives them." |
| Caller-ID detail | `runtime/tasks.py` + the state note | The caller's number pre-fills every task's `phone` detail as *unconfirmed*; the state note says "confirm the number ending 678 instead of asking for it". One yes instead of eleven spoken digits |
| `end_call` tool (voice only) | `tools/call.py` | The model can end a finished call; the channel says the scripted goodbye |
| Voice marks in the trace | `runtime/trace.py` + the glue | speech ended → endpoint → transcript final → first token → first TTS byte → first audio out → first answer audio |

**Barge-in, append-only.** The caller cuts in after the first of three offered
times. Don't edit the saved assistant message down to what they heard.
Editing earlier history breaks Groq's prefix cache and makes the transcript
disagree with the trace. Instead the turn stores `agent_text` (generated) and
`heard_text` (what played), and when they differ the next turn starts with a
context note:

> The caller interrupted your last reply. They heard only: "We have 7 pm free…"

The dashboard shows both. This is Frontdesk's §4.3 fix, adapted to
append-only transcripts. And because the commit check reads the heard text, a
read-back that was cut off followed by "yes" commits nothing.

---

## 4. The latency budget

What Frontdesk measured on a live call, and what the v3.1 design changes:

| Stage | Frontdesk (measured) | HeyDesk |
|---|---|---|
| Waiting to be sure the caller stopped | ~700 ms | The framework's semantic turn detection; tune it |
| Judgment + tools + model to first token | **1.2–3.0 s**: the model round trip dominated | Guard in parallel with a deadline (≈0 ms added), cached prompt + tools, `reasoning_effort` `low`, Groq's fast inference, first sentence spoken as soon as it's complete |
| Dead air during tools and the second model call | — | Covered by progress lines: first *sound* ≤ ~0.8 s even when the answer takes longer (07 §3) |
| Speech rendering | 3–19 ms | Similar; scripted lines can be pre-rendered |
| Last word → first sound (P50) | **2.2 s**, against a target of ~0.8 s | **Measure first** (day 26); write it in the README |

Frontdesk's lesson: *"the voice stack is not the problem; the round trip to
the model is."* The biggest levers are the cached prefix, `low` effort,
speaking sentence by sentence, and progress lines. If that's still too slow,
07 §2 lists the next steps in order; the first is a faster model *for voice
agents only* (e.g. `openai/gpt-oss-20b`). It's a per-agent setting, so that's
a config change, not a code change.

---

## 5. Building it: the order (M4, days 22–27)

The day-by-day checks are in [PLAN.md](../PLAN.md#m4--voice-days-2227).

1. **Spike** (day 22): a browser call that echoes back, on the candidate
   framework(s). Pick the framework, STT and TTS on first-sound latency and
   on how cleanly `start_turn()` plugs in as the LLM step.
2. Wire `pipeline.py` to `start_turn()`; a Call button on the dev page.
   **Place one order by voice.**
3. The voice style block and spoken forms end to end; progress lines spoken;
   keyword boosting; `speakable.py`.
4. Barge-in: the interrupt signal, heard text, the barge-in note, backchannel
   settings. Test: interrupt a list of three times after the first.
5. Silence reprompt, `end_call`, the disclosure line; trace marks; measure
   last-word → first-sound. Write it down.
6. Run every scenario with `channel="voice"` (text in, text out). They should
   pass unchanged; if not, something voice-shaped leaked into the core.
7. Stretch: a phone number through the framework's SIP/telephony, caller ID
   into `caller_ref`, the caller-ID detail.
