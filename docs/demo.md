# The demo

About seven minutes, on the online shop (the seed's Nakshi Threads), then the owner's and
the admin's screens. Rehearse it twice in a row without a hitch, and record one good run as
the backup video.

## Before

In `.env`: the real providers (`LLM_PROVIDER=gigalogy`, `EMBEDDER=gemini`) and
the shop as the dev agent:

```bash
DEV_AGENT_SEED=examples/agents/shop.yaml
```

Then a fresh database and both servers, with no reload so a saved file can't restart it
mid-demo:

```bash
scripts/reset.sh                    # the database, demo@heydesk.test and admin@heydesk.test
scripts/dev.sh all --no-reload      # the backend on :8000, the frontend on :8080
```

Open three tabs:

1. The customer chat: <http://localhost:8080/chat/dev-shop>
2. The owner, logged in as `demo@heydesk.test` / `demo password`: <http://localhost:8080>
3. The admin, in a private window, as `admin@heydesk.test` / `admin password`

Keep a terminal with `scripts/dev.sh logs api` open: each `turn finished` line sums up a turn's
trace (the steps and tools, the time, the tokens), and a failure shows in yellow or red.

## 1. The problem (30 s)

Small Bangladeshi shops sell through a Facebook page, and someone answers the same questions all
day: the price, the size, the stock, the delivery charge, "where's my order?". HeyDesk's agent
answers them and takes the order, in the buyer's own mix of Bangla and English.

## 2. A customer orders (2 min)

In the customer chat, one line at a time:

1. *Cotton panjabi L size white ache?* → yes, and only 3 left (Python's count, per size and colour).
2. *L navy?* → sold out, and it offers what's left.
3. *1ta L white, Mirpur 10 e delivery* → Mirpur 10 is Inside Dhaka: ৳60 delivery, ৳1,910 in
   all, and ৳1,150 more would make delivery free.
4. *dam kom hobe?* → a polite no: prices are fixed, and it never invents a discount.
5. Give a name and a mobile (`01712345678`) → it reads the whole order back and asks.
6. *ji* → order **O-101**, and what happens next.

Say: the model only talks. Python found the area, the charge, the total and the stock, and
nothing was saved until the read-back got a clear yes. And Python saves only the exact details
it read back.

## 3. The owner (1.5 min)

In the owner's tab:

1. The **Inbox** has O-101. Confirm it (the shop calls the buyer first), then mark it shipped.
2. Back in the customer chat: *amar order kothay?* → shipped, with no code needed in the same chat.
3. The products page: change the Silk Panjabi's price and save. In the chat: *silk panjabi er
   dam koto?* → the new price. An edit is live on the next message; there's no publish step.

## 4. The admin (1 min)

In the admin's window:

1. **Overview**: today's turns, tokens, cost and the first-reply times, added up from the saved
   turns (never the customers' words).
2. **Businesses**: find **Dev** (the dev workspace; its agent is Nakshi Threads) and open its
   page: its channels, payments, documents and week of use. **Open** shows the owner's screens under a banner, and every change lands in the
   audit log.

## 5. How it's built (1.5 min)

Show the README's diagram and say:

- The shop is one folder (`shop/`): its prompt, tools, tables and routes.
- The engine (`runtime/`) knows no framework or database; the providers sit behind small
  interfaces, so Maira and Groq swap with one setting.
- `scripts/check.sh` runs the tests (offline, about 15 seconds), lint, types and the layer
  rules, and CI runs it on every push.

## If something goes wrong

- **The model is slow or down:** set `LLM_PROVIDER=groq` and restart, or play the backup video.
- **A stuck conversation:** reload the chat page; it starts a new one.
