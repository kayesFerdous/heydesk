# Decision log

Newest last. Each entry: what, why, and which doc holds the detail.

| # | Date | Decision | Why | Detail |
|---|---|---|---|---|
| D1 | 2026-09-30 | **Voice channel in M4 (week 4); the Vue app moves after the month.** The voice-ready core goes into M1–M2 | Voice is the product's end goal and proves "one brain, many channels"; Swagger is enough of a dashboard for the demo | This file; 04 |
| D2 | 2026-09-30 | **Turn lease at the start of a turn** (conditional `UPDATE`), plus the version check at save | A version check alone notices a concurrent turn only *after* both ran the model and tools | 02 §8, 07 §4 |
| D3 | 2026-09-30 | **A turn runs in its own task; listeners subscribe. Interruption is a signal** | Starlette cancels a streaming generator on disconnect, which would lose a turn after `place_order` wrote. Barge-in must stop speech without losing the turn | 02 §7 §10, 07 §4 |
| D4 | 2026-09-30 | **The transcript is stored per turn** (`turns.messages`, insert-only); `conversations.transcript` is dropped | Append-only by construction, no growing blob rewritten every turn, and the review page shows exactly what the model saw | 02 §9 |
| D5 | 2026-09-30 | **The hand-off guard fails open after a deadline; the commit check fails closed** | Otherwise a Jev outage or slow answer hands off every turn or delays every first word | 02 §6, 07 §9 |
| D6 | 2026-09-30 | **`Progress` events from a code policy**: fixed phrases, deterministic rotation, never in the transcript; web shows labels, voice speaks text | "Let me check that" must be instant, identical and safe, and must not be answered by the model later | 07 §3 |
| D7 | 2026-09-30 | **Display and spoken forms for every value** (`runtime/speech.py`); the channel picks in code | Models convert numbers to words unreliably; the read-back on voice must be exact | 07 §5 |
| D8 | 2026-09-30 | **The greeting is stored as the first assistant message** | Otherwise the model doesn't know the visitor saw it, and greets twice | 07 §10 |
| D9 | 2026-09-30 | **Current time in the state note**; a calendar note is appended if the date changes | "Are you open now?" must stay right; the first-turn context note goes stale | 07 §11 |
| D10 | 2026-09-30 | **`client_message_id`** on every chat message, unique per conversation | A retry after a network blip must not run a turn (and an order) twice | 02 §8, 07 §4 |
| D11 | 2026-09-30 | **mypy and CI from M0** | `Confirmed[T]` makes an unconfirmed write a *type error* only if a type checker runs | 03 §8 |
| D12 | 2026-09-30 | **Structured JSON logs from M0** (ids only, no message text or phone numbers) | Log context is painful to retrofit; PII rules are easiest from the first line | 06 §3 #4 |
| D13 | 2026-09-30 | **Every tool has `timeout_s`; `run_tool` enforces it** | "Checking another source" must never mean a hung conversation | 02 §5, 07 §3 |

A design change adds a line here **and** updates the doc it touches, in the same commit.
