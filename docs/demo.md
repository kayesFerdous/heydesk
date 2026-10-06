# The five-minute demo

What to show, in order, and what to say. Rehearse it twice in a row without a
hitch, and record one good run as the backup video.

## Before

```bash
scripts/reset.sh                       # a fresh database and the demo login
uv run uvicorn heydesk.main:app        # no --reload: a file save mustn't restart it mid-demo
```

- `.env`: the real model (`LLM_PROVIDER=gigalogy` or `groq`) and `DECIDER=jev`.
- Chrome on <http://localhost:8000>, the microphone allowed, the sound on.
- A second tab on <http://localhost:8000/docs>, **Authorize**d with the token
  `scripts/reset.sh` printed.
- A terminal showing the server log: the `turn finished` lines are the trace.

## 1. A web order (1 min)

Type, one line at a time:

1. *what's good here?* → it recommends from the menu, with prices.
2. *two chicken biryani and a borhani* → **৳1,030**: Python added it up, not the model.
3. *pickup at 7pm, I'm Kayes, 01712345678* → it reads everything back and asks.
4. *yes* → **order 101**.

Say: nothing is saved until it has read the order back and heard a clear yes.
Jev checks both, and if Jev is down, nothing is saved.

## 2. A voice order (1 min)

Press **Call**. It greets you. Say:

1. *I'd like one mutton kacchi for pickup at 8 tonight.*
2. Give your name and number when it asks.
3. *Yes, that's right.* → you hear "Placing that now", then the order number.

Point out the total spoken in words, and the phone number read back digit by digit.

## 3. Interrupting it (30 s)

Still on the call: *what's on the menu?* While it's listing, talk over it:
*just the drinks, please*. It stops at once and answers about the drinks.

## 4. The hand-off (30 s)

Type (or say) *can I talk to a person?* → the polite line and the phone number.
In Swagger, `GET /api/conversations?status=needs_human` shows that conversation.

## 5. Edit the agent (1.5 min)

In Swagger:

1. `GET /api/orders` → orders 101 and 102, with what was said in
   `GET /api/conversations/{conversation_id}`.
2. `GET /api/menu`, change Chicken Biryani's `price` to `47500`,
   and send it back with `PUT /api/menu`.
3. Back in the chat, a new conversation: *how much is the chicken biryani?* → **৳475**.

Say: an edit is live on the next message. There's no publish step.

## If something goes wrong

- **No sound on the call:** hang up and call again; check the server log for `chorus:` lines.
- **The model is slow or down:** switch `LLM_PROVIDER` and restart, or play the backup video.
- **A stuck conversation:** reload the page; it starts a new one.
