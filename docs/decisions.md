# Decision log

Newest last. Each entry: what, and why. Add a line when a design changes, in
the same commit as the change.

| # | Date | Decision | Why |
|---|---|---|---|
| D1 | 2026-09-30 | **Voice is the main demo**, built in M2, before the builder API | It's the riskiest part and the one that shows best; doing it early keeps the demo safe |
| D2 | 2026-09-30 | **One turn at a time per conversation**: a lease claimed at the start of a turn (conditional `UPDATE`), 409 otherwise | Two messages at once would run the model twice and could place two orders |
| D3 | 2026-10-01 | **The turn runs inside its listener** (the SSE response or the voice pipeline) | Simple. A tab closed mid-reply loses that turn; acceptable for the MVP |
| D4 | 2026-09-30 | **The transcript is stored per turn** (`turns.messages`), only ever added to | No growing blob to rewrite, and each turn shows exactly what the model saw |
| D5 | 2026-10-01 | **Jev (the Decider) does the commit check, and it fails closed.** Hand-off is a tool the model calls | Saving an order is the one step that can't be undone; an independent check guards it |
| D6 | 2026-10-01 | **Waiting lines are scripted, never in the transcript**: web shows a tool's `label`, voice says its `filler` | The line must be instant and safe; a model that sees "let me check" in its history starts answering it |
| D7 | 2026-09-30 | **Display and spoken forms for every value** (`runtime/speech.py`); the channel picks in code | Models convert numbers to words unreliably; a read-back on voice must be exact |
| D8 | 2026-09-30 | **The greeting is stored as the first assistant message** | Otherwise the model doesn't know the visitor saw it, and greets twice |
| D9 | 2026-09-30 | **The current time is in the state note** | "Are you open now?" stays right in a long conversation |
| D10 | 2026-09-30 | **mypy, CI and JSON logs from the start** | Cheap now; `Confirmed[T]` only protects anything if a type checker runs |
| D11 | 2026-09-30 | **Every tool has a timeout** | A slow source must never mean a hung conversation |
| D12 | 2026-10-01 | **`publish` saves a new version only if the built result (config, prompt, tool specs) changed** | No duplicate versions; a change to the platform rules reaches the dev agent on restart |
| D13 | 2026-10-01 | **MVP scope: edge cases not worth their code are left out** (design §12) | The time goes into the normal path and the voice demo |
| D14 | 2026-10-01 | **Gigalogy's Maira is the default talking model; Groq stays one setting away** (`LLM_PROVIDER`) | The owner's choice. Maira takes one text query: no streaming, no tool calls, no prompt cache. So from day 5, ordering needs `LLM_PROVIDER=groq` |
| D15 | 2026-10-01 | **Simpler storage, no caching.** One `config` per agent, no versions or publish (replaces D12). The transcript is the conversation's `messages` list (replaces D4); no turn lease (replaces D2); each turn's trace is a log line. The system prompt is built every turn with the date and time at the end | The owner's call for the MVP: fewer tables and concepts to learn. Costs: an edit reaches conversations already in progress, two messages sent at once would both run, and no prompt cache |
| D16 | 2026-10-01 | **Gigalogy calls tools too: as JSON in its reply** (changes D14). The query lists the tools and asks for `{"tool": …, "arguments": …}`; `GigalogyLLM` turns that into a `ToolCall`, so the runtime is the same for every provider | The owner wants ordering on Maira, the default. Costs: less reliable than native tool calls (bad JSON comes back as a validation hint), and each tool step is another whole, unstreamed request |
| D17 | 2026-10-01 | **Items live in their own `items` table, in one shape for every preset.** Up to `MAX_ITEMS_IN_PROMPT` (60) they're listed in the prompt; above that the prompt gives the categories and the model uses `search_items` | Tools work for any business; a business edits one item, not a config blob. A short list in the prompt lets the model recommend with no extra round trip; search is for catalogues too big to list |
| D18 | 2026-10-02 | **Voice: Pipecat over its own WebRTC (SmallWebRTC), Silero VAD, Gigalogy's Chorus for speech-to-text, Piper for text-to-speech for now** (Q3, Q4) | Pipecat runs in our process and talks to the browser directly: no LiveKit server or account to fail in a live demo, and only the owner's paid Gigalogy endpoints cost money. Chorus is upload-then-poll, not a stream, so each utterance is sent when the VAD hears 0.5s of silence. Measured 2 Oct on the echo call: Chorus 1.8–2.4s warm (6–8s when it's slow), Piper 0.2s, first sound 2.1s after the last word. Piper is free and local until the owner's TTS is added; a free local Whisper can replace Chorus behind the same class if it's too slow |
| D19 | 2026-10-02 | **No `voice/sentences.py` or `voice/pipeline.py`: the turn runs inside `voice/call.py`, and the reply's tokens go straight to Pipecat's TTS**, which already speaks sentence by sentence | Pipecat does the sentence cutting, so our own copy would be code to keep in step for nothing. One file holds the whole call |
