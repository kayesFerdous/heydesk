# 07 — Conversation experience: natural, deterministic, never silent

How a HeyDesk conversation should *feel*, on web chat now and on voice in
week 4, and the code rules that make it feel that way. 02 says how the engine
works; this doc says what the visitor experiences and which part of the
engine is responsible for each piece of it.

- Engine: [02-build-guide.md](02-build-guide.md) (§3 rules, §5 tools, §7 the turn)
- Voice channel: [04-voice-later.md](04-voice-later.md)
- The plan: [../PLAN.md](../PLAN.md); the decision log: [decisions.md](decisions.md)

Everything here is designed now. Most of it is **built in M1–M2** because it
also improves web chat; the audio-only parts are built in **M4 (voice)**. §13
says which is which.

---

## 0. The bar

What a visitor or caller should feel, as numbers where possible:

| Promise | Web | Voice |
|---|---|---|
| **Answered quickly** | First token P50 ≤ 1.0 s | First sound P50 ≤ 0.8 s (a filler counts); first *answer* P50 ≤ 1.6 s, P95 ≤ 2.5 s |
| **Never left in silence** | A status line within 100 ms of a tool starting | No more than ~1 s of dead air in a turn, ever |
| **Never asked twice** | A detail in *Have* is never asked for again | Same |
| **Never told something false** | Prices, times, availability and "it's placed" come from tools | Same, read aloud in words |
| **Same situation, same behaviour** | Same questions in the same order, same commits | Same, plus identical scripted lines |
| **Sounds like a person** | Short, warm, answers side questions first | One or two sentences, no lists, numbers as words |

These are targets to measure, not guesses to trust. Frontdesk measured 2.2 s
P50 voice-to-voice against a 0.8 s target; the trace fields in §2 are how we
find out where HeyDesk lands, and the README records the real numbers.

---

## 1. Natural but deterministic: who decides what

"Deterministic" doesn't mean the same *words* every time. It means the same
*behaviour*: the same questions asked in the same order, the same facts, and
the same moment of commit. "Natural" means the words fit the moment. HeyDesk
gets both by splitting the work three ways:

| Decision | Who | Why |
|---|---|---|
| What to collect next, when a task is complete, when to commit | **Code**: `next_step()`, the state note, the finishing tools | Customers edit the task list; code runs it (02 §7) |
| Facts: prices, totals, dates, free times, order numbers | **Code**: tools, returned in words | The model never computes or invents a fact (rule 1) |
| Whether to hand off | **Jev guard + code** | Escalation can't be edited away by a prompt |
| The wording of replies, answering side questions, recommendations | **The model**, steered by the state note | This is what sounds human |
| **Scripted moments** | **Code, verbatim, no model call** | These must be instant, identical and safe |

**Scripted moments** are the lines code says by itself:

| Moment | Where the words come from |
|---|---|
| Greeting | `AgentConfig.greeting` (shown instantly; stored as the first assistant message, §10) |
| Waiting phrases and acknowledgements | The progress policy (§3) |
| Hand-off | `AgentConfig.handoff.message` |
| Failure apology | Platform phrase, per channel (§9) |
| Turn cap / call length cap | Platform phrase + hand-off |
| Voice: disclosure, "are you still there?", goodbye | `AgentConfig.voice` (M4) |

They're instant (no model round trip), can be pre-rendered as audio on voice
(06 §4 layer 6), are identical on every run (so tests can assert them exactly),
and never say anything a tool didn't confirm.

**The levers of determinism**, strongest first:

1. **Structure**: tasks, `next_step()`, the state note's *Next*. The model is
   told exactly one next thing to do.
2. **Tools that refuse**: bad items, bad dates, commits without a confirmed
   read-back all come back as errors the model has to act on.
3. **Scenario tests on outcomes** (02 §16): rows written, questions asked,
   nothing committed before a yes.
4. **A stable prompt**: built at publish, byte-identical every turn.
5. **Model settings last.** `reasoning_effort="low"`. Don't lower the
   temperature below the model's recommended default to "make it
   deterministic": for reasoning models that tends to hurt quality more than it
   helps consistency. Structure does that job. Measure before changing it.

**The levers of naturalness**, in the platform rules and presets:

- Answer what they asked first, then continue with *Next*.
- One question at a time (or two that belong together: name and phone).
- Accept details in any order, and several at once.
- Use the visitor's own words for things ("the spicy one"), the menu's names for items.
- Vary acknowledgements; never start two replies in a row the same way.
- Short: web two or three sentences; voice one or two.
- After a task finishes, close the loop once: "Anything else I can help with?"

---

## 2. Latency: where the time goes

A **web** turn where the visitor gives a detail:

```
send ─► guard ∥ model step 1 (reads, calls set_details) ─► tool (~5 ms) ─► model step 2 ─► first token
        ~0 ms     300–700 ms                                                   300–700 ms
```

A **voice** turn adds both ends:

| Stage | Typical | Our lever |
|---|---|---|
| Caller stops → endpoint decided | 200–500 ms | The framework's semantic turn detector, tuned (§7) |
| Transcript final | 50–150 ms (overlaps) | STT choice |
| Guard | ~0 ms (in parallel, with a deadline) | 02 §6 |
| Model step 1 → tool call | 300–700 ms | Cached prefix, `low` effort, faster model for voice agents |
| Tool | 5 ms (DB) to seconds (external) | Timeouts; fillers (§3) |
| Model step 2 → first sentence | 300–800 ms | Same as step 1; sentence streaming |
| TTS first byte | 40–300 ms | Provider choice; pre-rendered scripted lines |

**The two-call cost is the biggest voice risk.** Any turn where the visitor
gives a detail makes two model calls before the answer (02 §7, "What it
costs"). On web that's invisible behind streaming. On voice it's 1–1.5 s of
silence. The mitigations, in the order we'll try them:

1. **An acknowledgement** ("Got it.") the moment a detail is accepted (§3). This
   covers the gap and is what a person does anyway.
2. **A faster model for voice agents** (`openai/gpt-oss-20b`). This is a per-agent setting.
3. **Later, measured:** let a step that *only* saved details and already
   produced a full reply end the turn without a second model call. It saves a
   whole round trip but has edge cases (a rejected detail), so it needs traces first.

**What we measure** (in `turns.trace`, 02 §14):

- per model step: `ttft_ms`, `ms`, tokens (cached / uncached / output);
- per tool: `ms`, `ok`, `timed_out`;
- per turn: `first_output_ms` (first token *or* first progress line), `progress[]`;
- voice marks (M4): `speech_ended`, `endpoint_decided`, `transcript_final`,
  `first_token`, `first_tts_byte`, `first_audio_out`, `first_answer_audio`.

---

## 3. Never leave them waiting: the progress policy

This is the "let me check that for you" requirement, designed so it's
**natural** (it fits the moment), **deterministic** (code picks it, the same
way every time) and **safe** (it never states a fact or promises an outcome).

### The waits in a turn

| Wait | When | Example |
|---|---|---|
| **Thinking** | The model hasn't produced anything yet (reasoning, first-token time) | Every turn's first step |
| **Tool** | A tool is running | `check_availability` against a calendar |
| **Between steps** | A tool returned; the next model step is thinking | After `set_details`, before the next question |

### The design

- `runtime/progress.py` holds a **pure policy**: given the channel profile,
  the kind of wait, the elapsed time, the tool's metadata and what's already
  been said this turn, it decides whether to emit a line now and which one.
  It is tested with a fake clock.
- The loop wraps each wait (the model stream's next event, a running tool) in
  `progress.watch(...)`: an `asyncio.wait` with a timeout that **yields a
  `Progress` event when a threshold passes, without cancelling the work**.
- The event carries both forms, and the channel picks one:

```python
@dataclass(frozen=True)
class Progress:
    kind: Literal["wait", "ack", "still"]
    label: str          # web status line: "Checking availability…"
    text: str           # voice line: "Let me check what's free."
    tool: str | None
```

- **The runtime proposes; the channel disposes.** The runtime emits at its
  thresholds. Web shows the label. Voice speaks the text, unless it's still
  playing audio or the caller is talking, in which case it drops the line.
  Only the channel knows that.

### Channel profiles

| | Web | Voice |
|---|---|---|
| Typing indicator | From send until the first token | — |
| Tool status line (`label`) | At tool start | — |
| **Wait** line for a tool | Never spoken | At tool start if `expected_ms ≥ 300`; otherwise once the tool has run 700 ms |
| **Wait / ack** line while thinking | Never spoken | When 1,000 ms pass with nothing said since the caller stopped or since the last line |
| **Still** line | Never spoken | Once, 3,000 ms after the last line if still waiting |
| Lines per turn, max | — | 2 (+1 still) |

The numbers are starting points, stored as data in the profile so the traces
can tune them. Frontdesk used 300 ms and 1.5 s; the reasoning is the same: a
filler over a 120 ms write makes the call *longer* and sounds like hesitation.

### Which words

- **Kinds.** *Wait* phrases before an answer ("Let me check that."). *Ack*
  phrases right after the visitor's details were accepted: the last tool was a
  successful `set_details` or `update_order` with nothing rejected ("Got it.",
  "Perfect."). *Still* phrases for a long wait ("Still checking, thanks for
  waiting.").
- **Sources, most specific first:** the customer's override on the tool
  (`ToolSetting.fillers`), the tool's defaults (`Tool.fillers`), the agent's
  general phrases (`AgentConfig.voice.fillers`, filled from the preset).
  Example defaults for `check_availability`: "Let me check what's free.", "One
  moment, I'm looking at the calendar."
- **Deterministic rotation.** `phrases[n % len(phrases)]`, where `n` is a
  counter kept in the conversation state. It never repeats back to back, and a
  replayed scenario gets exactly the same lines.
- **Rules for every phrase** (checked by `AgentService.validate`): at most 8
  words; one full sentence ending in a period, so TTS renders it in one piece
  (a Frontdesk lesson); no facts, no numbers, no promises ("I've booked…"), no
  questions (a question invites an answer while we're busy).
- **Later:** templates filled from *validated* tool input, so the line is
  specific: "Let me check Friday for you." The input is only used after
  `run_tool` has validated it, so a template can't read out a bad value.

### Never in the transcript

A progress line is **not** something the agent said. It's the sound of it not
having answered yet. It goes into the trace (`progress: [{kind, text, at_ms}]`)
and into `turns.progress` for the review page, **never** into the messages the
model sees. Frontdesk's lesson: a model that reads "Let me check that" in its
own history starts answering it.

The other half is a platform rule: *"While tools run, the system says short
waiting phrases for you. Never say 'let me check', 'one moment' or similar
yourself; start your reply with the answer."* A scenario assertion enforces it
(§12). If the model streamed text in the current step anyway, the policy skips
its next wait line; the still line still applies.

### One voice turn, end to end

The caller asks, *"Do you have anything Friday evening?"*

| t (ms) | What happens | Caller hears |
|---|---|---|
| 0 | Caller stops speaking | |
| 300 | Endpoint decided, transcript final; guard ∥ model step 1 | |
| 850 | Model calls `check_availability` (no text). `expected_ms` 400 ≥ 300 → **wait** line | "Let me check what's free." (850–1,900) |
| 1,250 | Tool returns | |
| 1,750 | Model step 2's first sentence is complete | |
| 1,950 | The filler finishes; the answer starts | "Friday evening I have 6:30 or 8 o'clock…" |

First sound at 850 ms; first answer at 1.95 s. Without the filler the caller
would have heard 1.9 s of nothing and wondered whether the line dropped.

### Slow sources: timeouts

"Checking another source" means a wait we don't control, so **every tool has
a `timeout_s`** (default 5 s; custom webhook tools later get a configurable
value up to 10 s). `run_tool` enforces it with `asyncio.timeout`. A timeout
becomes an error result, never a hang:

```json
{"error": "timeout", "hint": "The booking system is slow. Say so, and offer to take a message or try again."}
```

The model phrases that. The caller has already heard a wait line and a still
line, so a slow source never means silence.

---

## 4. Turns that survive interruptions and disconnects

Three things happen to real conversations that a demo never shows:

1. **The browser closes mid-reply.** Starlette cancels a streaming response's
   generator when the client disconnects. If the turn runs *inside* that
   generator, it dies at the next `await`: no `TurnEnded`, nothing saved, even
   though `place_order` may already have written the order.
2. **The caller barges in.** Voice must stop the reply at once, and the turn
   must still be saved, with what the caller actually heard.
3. **A second message arrives mid-reply** (two tabs, a double send, a caller
   talking over the agent). If both turns run, both call the model and tools;
   an optimistic version check at save only notices *after* the tools ran.

The design (decisions D2–D4 in [PLAN.md](../PLAN.md)):

- **A turn lease at the start.** `start_turn` claims the conversation with a
  conditional update: `UPDATE conversations SET turn_lease_until = now + 120 s,
  version = version + 1 WHERE id = … AND workspace_id = … AND (turn_lease_until IS NULL OR
  turn_lease_until < now)`. No row updated means `409 reply_in_progress`, and
  **no model call is made**. Saving releases the lease. If the process
  crashes, the lease expires. It's portable SQL and already safe for several
  processes.
- **The turn runs in its own task, not in the listener.** `ConversationService`
  starts the turn as an `asyncio.Task` that owns persistence. The SSE response
  (or the voice pipeline) *subscribes* to its events through a queue. If the
  listener goes away, the turn still reaches `TurnEnded` and saves. It can't
  run away: `MAX_STEPS`, `max_completion_tokens` and tool timeouts bound it.
- **Interruption is a signal, not a cancel.** `turn.interrupt()` sets an
  event. The loop checks it at every token and before every model call. On
  interrupt:
  - it stops the model stream now;
  - tool calls already requested in this step **all run to completion** (a
    commit is never cut in half, and every tool call needs its result for the
    transcript to stay valid);
  - no new model call starts;
  - `safe_close` + `TurnEnded(interrupted=True)`.
- **Heard vs generated.** The channel reports what reached the person: on
  voice, the speech spans that finished playing; on web, the text streamed
  before a disconnect, otherwise all of it. The turn stores both (`agent_text`,
  `heard_text`). When they differ, the next turn starts with an appended note:
  *"The caller interrupted your last reply. They heard only: '…'"* (04 §3).
- **The commit check reads the heard text.** `confirm()` asks Jev whether the
  *heard* previous message read the details back. A read-back cut off halfway
  followed by "yes" is not a confirmed read-back, and nothing is written.
- **Idempotent retries.** The browser sends a `client_message_id` (a uuid)
  with every message; turns have UNIQUE(`conversation_id`, `client_message_id`).
  A retry after a network blip replays the saved reply instead of running the
  turn twice.

---

## 5. Speaking is not writing

Tools already return values "in words" (02 §5). Voice needs a second form,
because "৳1,250" and "01712-345678" read aloud are nonsense or worse.

| Value | Display (web) | Spoken (voice) |
|---|---|---|
| Money | ৳1,250 | one thousand two hundred and fifty taka |
| Time | 7:30 pm | seven thirty in the evening |
| Date | Friday 3 October | Friday the third of October |
| Phone | 01712-345678 | zero one seven one two, three four five, six seven eight |
| Order number | A-104 | A one zero four |
| Quantity line | 2 × Chicken Biryani | two chicken biryanis |

- **`runtime/speech.py`** holds pure functions for both forms, one per value
  kind, with tests. Every detail type implements `display(value)` and
  `spoken(value)`.
- **The channel picks the form in code, not the model.** `ToolContext` and the
  state note know the channel. On voice, the read-back in the state note is
  *already spoken form*, so the model reads words, not digits. Models are
  unreliable at converting numbers to words mid-sentence; code isn't.
- **The voice style block** in the context note (02 §7): no lists, markdown,
  emoji or links; one or two sentences, about 35 words at most; at most three
  options at a time; say numbers the way the state note gives them.
- **A last-line normaliser before TTS** (`voice/speakable.py`, M4) strips
  markdown and bullets, expands leftover symbols, and turns URLs into "our
  website". It's a safety net for anything the rules missed, not the primary mechanism.
- **Pronunciation, later:** a per-agent lexicon (`"Borhani": "bor-HA-nee"`)
  passed to the TTS provider's alias or phoneme feature.

This is cheap to build now (M1), it's tested like any other pure function,
and it's the difference between a voice agent that sounds read-aloud and one
that sounds spoken.

---

## 6. Hearing right (voice, M4)

About one named entity in six is misheard on real phone audio (Frontdesk
§4.4). The commit check catches a wrong value before it's saved; these make
the wrong value rarer:

- **Keyword boosting at call start**: business name, menu item names, staff
  and doctor names, service names, all from the published knowledge. One call,
  the cheapest accuracy there is.
- **Phone numbers**: on a phone call, the caller ID pre-fills `phone` as
  *unconfirmed*, and the state note says "confirm the number ending 678". That's
  one yes instead of eleven spoken digits. The `phone` detail type parses spoken
  digits ("oh one seven…", "double five"), as Frontdesk's `spoken.digits` does.
- **Names**: on voice, the read-back spells a name the caller gave
  ("K-A-Y-E-S"), and the `person_name` type understands phonetic spelling ("K
  for kilo").
- **Low confidence**: the channel passes the STT's weakest-word confidence
  with the transcript. Below a threshold, a detail saved that turn is marked
  *unconfirmed* and the state note asks to check it. The threshold gets tuned from traces.

---

## 7. Turn-taking (voice, M4)

- **Semantic endpointing.** Use the framework's turn-detection model, not a
  bare silence timer. People pause mid-sentence ("my number is… 017…").
  Measure false cut-offs and needless waits on recorded calls.
- **More patience mid-entity.** `next_step()` knows what's being collected.
  The state exposes `expects: "phone" | "name" | "free_text" | None`, and the
  voice channel lengthens the endpointing delay while a phone number or a
  spelling is expected. The behaviour comes from code, not guesswork.
- **Backchannels aren't interruptions.** "Mm-hmm", "yeah" and "okay" while
  the agent talks must not stop it. Require a minimum number of words or a
  minimum duration before treating speech as barge-in; if an "interruption"
  turns out to be empty, resume. Both frameworks we're considering have
  settings for this (names vary by version).
- **Barge-in**: audio stops within ~200 ms; the turn is interrupted by the
  signal in §4; the heard text is recorded.
- **No speculative turns that can write.** Some frameworks can start the
  model before the caller has finished ("preemptive generation"). Our turns
  run tools and change state, so a turn starts only on a final transcript.
  Speculation is allowed only for read-only prefetch (06 §4 layer 5).

---

## 8. Silence, endings and hand-off (voice, M4)

- **AI disclosure**: the first line of a call says it's a virtual assistant
  (and mentions recording if enabled). It's a scripted line, configurable per
  agent, and a legal requirement in many places.
- **Caller silence**: 6 s of silence after the agent asked something → a
  scripted "Are you still there?"; 6 s more → a scripted goodbye, then hang up.
  The timings are per-agent settings.
- **Ending well**: after a task finishes, the state note says to ask once
  whether there's anything else (web too). A voice-only `end_call` tool lets
  the model end the call when the caller says goodbye; the channel then plays
  the scripted goodbye and hangs up.
- **Hand-off**: web shows the hand-off card. Voice transfers the call to
  `handoff.phone` when it's set and the business is open; otherwise it takes a
  message (the `message` task). A scripted line comes first ("I'll put you
  through to the team now.").
- **Caps**: a maximum call length (e.g. 15 minutes) and `MAX_TURNS` both end
  with a scripted wrap-up and the hand-off contact.

---

## 9. When things go wrong

A failure is never silence and never a stack trace. It's a polite scripted
line plus a trace entry.

| Failure | What the person gets | Engine |
|---|---|---|
| Model down before the first token | Apology + hand-off card (web), apology + transfer or message (voice) | `LLMUnavailable` → `safe_close` + hand-off. Production: one retry, then `FallbackLLM` (06 §5) |
| Model fails mid-reply | What already streamed, then the apology | The partial text is kept in `agent_text` and `heard_text`; the transcript stays valid |
| **Jev guard slow or down** | Nothing: the reply goes on | **Fails open after a deadline** (~800 ms), traced. The guard protects; it must not become a single point of failure |
| Jev commit check down | "I couldn't confirm that just now. Could you say yes once more?" | **Fails closed**: nothing written (02 §5) |
| Tool timeout | A wait line, a still line, then the model's words | Error result with a hint (§3) |
| Tool bug (exception) | The model is told the action failed, and offers the hand-off | `run_tool` catches it, traces it, returns `internal_error` |
| Turn cap / `MAX_STEPS` | Scripted wrap-up + hand-off | Already in 02 §3 rule 9 |

---

## 10. Web chat experience (M1–M2)

- **Greeting**: shown the moment the page loads (from `GET /api/chat/{key}`)
  and **stored as the first assistant message** of the conversation, so the
  model knows the visitor saw it and never greets twice.
- **Typing indicator** from send to the first token; the **status line** from
  `Progress.label` ("Checking availability…").
- **One message at a time**: the input is disabled while replying, and the
  server's turn lease backs that up.
- **Resume**: the conversation id lives in `localStorage`, so a reload
  continues the chat. A "Start over" link begins a new one.
- **Hand-off card** with the phone and email.
- **Errors**: a retry button that resends with the same `client_message_id`
  (§4), so a retry can never double an order.

---

## 11. Time and dates

- "Today" is the business's today (its timezone), as in Frontdesk.
- The **14-day calendar** goes in the first turn's context note (02 §7).
- **The current time goes in the state note** ("Now: Tue 29 Sep, 14:07"),
  because the state note is rebuilt on every call anyway. "Are you open now?"
  stays right in a conversation that lasts an hour. It's about ten tokens, outside the cached prefix.
- If the business-local **date changes** during a conversation, a fresh
  calendar note is *appended* (append-only, and the cache resets once).

---

## 12. Testing the experience

Scenario YAML (02 §16) checks outcomes. It also checks experience, with
assertions that don't depend on exact wording:

| Assertion | Catches |
|---|---|
| `no_repeat_greeting` | The model greeting again on turn 1 |
| `no_self_filler` | Model text containing "let me check", "one moment", "hold on"… |
| `max_reply_words` (per channel) | Rambling; voice replies over ~35 words |
| `asked_once` | The same detail asked twice |
| `read_back_before_commit` | A commit with no read-back in the heard text before it |
| `progress_not_in_transcript` | A progress line leaking into the model's messages |
| `voice_plain_text` | Markdown, lists, emoji or links in voice replies |

Plus unit tests:

- the progress policy with a fake clock: exactly which line at exactly which millisecond;
- `speech.py`: every value kind in both forms;
- the turn runner: a listener that disconnects mid-stream still produces a
  saved turn; an interrupt mid-stream produces `interrupted=True`, a valid
  transcript and the right heard text; a second message during a turn gets
  409 **without a model call**.

Live runs report latency from the traces: P50/P95 `ttft_ms` and
`first_output_ms` per preset, and the voice marks in M4. Voice scenarios also
run text-in/text-out with `channel="voice"` (04 §5 step 8).

---

## 13. What's built when

| Item | Milestone |
|---|---|
| `Progress` events, the policy, web status lines; `Tool.label/fillers/expected_ms/timeout_s`, timeouts enforced by `run_tool` | M1 |
| Greeting stored as the first assistant message; time in the state note | M1 |
| `runtime/speech.py`, display + spoken forms on detail types | M1 |
| Turn lease, turn runner, interrupt signal, heard text, `client_message_id` | M1 (day 6) |
| Guard deadline (fail open) | M2 (day 8) |
| Experience assertions in scenarios | M2 (day 12) |
| Failure phrases (§9) | M2 (days 13–14) |
| Voice profile thresholds spoken, sentence streaming, barge-in, keyword boosting, spell-back, silence reprompt, `end_call`, disclosure, voice marks | M4 |
| Filler templates from validated input; the single-call optimisation; prefetch; pre-rendered audio for scripted lines; warm transfer; pronunciation lexicon; DTMF; Bangla | After the month, each when traces or a customer ask for it |
