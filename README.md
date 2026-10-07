# HeyDesk

An AI front desk a business can build in minutes. It picks its kind of business (a restaurant,
a clinic, a receptionist for an office), fills in its menu or doctors, hours and FAQ, and adds
its own notes. Each kind has its own prompt and tools. Visitors talk to the agent by web chat or by
voice; it answers questions, takes orders and messages, and hands over to a
person when needed.

The model writes the words; code decides every price, total and time, and
nothing is saved without reading it back and hearing a yes.

## Status

Web chat and voice orders, the restaurant, clinic and receptionist desks, and the
API with login all work. What's left is the demo (see **[PLAN.md](PLAN.md)**).

## Docs

| | |
|---|---|
| [PLAN.md](PLAN.md) | What to build, day by day, and how to know it's done |
| [docs/design.md](docs/design.md) | How it works |
| [docs/decisions.md](docs/decisions.md) | Why it works that way |
| [docs/demo.md](docs/demo.md) | The five-minute demo, and `scripts/reset.sh` before it |
| [CLAUDE.md](CLAUDE.md) | Commands, rules and conventions |

## Running it

You need [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv.

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn heydesk.main:app --reload
```

Open <http://localhost:8000> to chat with the dev agent, or press **Call** to
talk to it (Chrome, on `localhost`, allows the microphone).

In `.env`, pick the models:

- **Talking:** `LLM_PROVIDER=gigalogy` with the three `GIGALOGY_*` keys, or
  `LLM_PROVIDER=groq` with `GROQ_API_KEY`. `fake` runs without keys but only
  says canned lines.
- **The commit check:** `DECIDER=jev` with `TYPESAFE_API_KEY`. With `fake`, no
  order or message is ever saved: it always says no.
- **Documents** (the knowledge base) use `GEMINI_API_KEY` for embeddings and Qdrant for search:
  set `QDRANT_URL` and `QDRANT_API_KEY` from your Qdrant Cloud cluster, or leave the URL empty to
  use a local folder. `EMBEDDER=fake` runs offline but only matches words.
- **Voice** uses Gigalogy's Chorus for speech-to-text (the `GIGALOGY_*` keys)
  and Piper for speech, which runs locally.

Tests are offline and free:

```bash
scripts/check.sh   # tests, lint, format, types, import rules
```

## Switching the kind of business

The dev agent is one desk (`restaurant`, `clinic` or `receptionist`) and a seed file. For the
clinic, set these in `.env` and restart:

```bash
DEV_AGENT_KIND=clinic
DEV_AGENT_SEED=examples/agents/clinic.yaml
```

Edit the seed (`examples/agents/*.yaml`) and restart to change the dev agent:
its name, address, hours, FAQ, and its menu or doctors. Any agent can be opened on the dev page
with `http://localhost:8000/?key=<public_key>`.

## Using the API

Swagger is at <http://localhost:8000/docs>. Everything that belongs to a
business needs a login token:

```bash
uv run heydesk create-user you@example.com "a long password"   # an owner of the dev business
uv run heydesk create-admin you@example.com "a long password"  # a platform admin (no business)
```

It prints a token. In Swagger, press **Authorize** and paste it. Then:

| | |
|---|---|
| `GET /api/desks` | The kinds of business, and the fields each one's agent has |
| `POST /api/agent` | Create your agent: `{"kind": "restaurant"}` is enough. `GET /api/agent` then lists what is `missing` |
| `GET /api/agent` | Your agent (a business has one), with its `public_key` (the chat URL) |
| `PATCH /api/agent` | Edit it: `{"faq": "…"}`, or `{"notes": "Be formal."}` for tone and rules. Live on the next message |
| `POST /api/agent/documents` | Add pasted text to the agent's knowledge: `{"title": "Policies", "text": "…"}`. `POST /api/agent/documents/file` takes a `.txt`, `.md` or `.pdf`. `GET` lists them and `DELETE /api/agent/documents/{id}` removes one. The agent searches them when a question isn't answered elsewhere |
| `GET/PUT /api/menu` | A restaurant's menu, replaced whole (prices in paisa) |
| `GET /api/orders` | A restaurant's orders, newest first; `?status=`, `?q=` (number, name or phone). `PATCH /api/orders/{id}` with `{"status": "ready"}`: new → ready → collected (or cancelled) |
| `GET/PUT /api/doctors` | A clinic's doctors, with their `days`, `start` and `end` |
| `GET /api/appointments` | A clinic's appointment requests (`A-101`). `PATCH` moves them: new → confirmed → done (or cancelled) |
| `GET /api/messages` | Messages for the team, from any kind of agent (`M-101`). `PATCH` with `{"status": "handled"}` |
| `GET /api/conversations/{id}` | What was said |
| `PUT /api/agent/telegram` | Put the agent on Telegram: `{"token": "…"}` from @BotFather. `GET` gives the `t.me` link to share; `DELETE` disconnects it. Needs `PUBLIC_URL` (below) |
| `GET /api/admin/businesses` | Admins only: every business. Suspend one with `PATCH /api/admin/businesses/{id}`; act inside one with an `X-Business: <id>` header on the routes here |

A new business signs up with `POST /api/auth/signup` and gets its own
workspace; it never sees another's agents. Errors always look like
`{"error": {"code": "…", "message": "…"}}`, with `fields` when a field is wrong.

## Telegram

The agent can answer on the business's own Telegram bot.

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Put your ngrok domain in `.env` as `PUBLIC_URL=https://….ngrok-free.app`, and start
   everything with `scripts/dev.sh`: the frontend opens on that link, and its dev server passes
   `/api` on to the backend, so Telegram reaches it there too.
3. `PUT /api/agent/telegram` with `{"token": "…"}` in Swagger.
4. Open the `link` it returns and press **Start**.

## Voice latency

The time from the caller's last word to the agent's first sound. To measure
it, keep the server's log, make about ten calls, then run the report:

```bash
uv run uvicorn heydesk.main:app 2>&1 | tee voice.log
uv run python scripts/latency.py voice.log
```

| | P50 | P95 |
|---|---|---|
| Last word → first sound | not measured yet | not measured yet |

So far: 2.1s on one echo call (2 Oct, no model). Chorus takes 1.8–2.4s of that.
