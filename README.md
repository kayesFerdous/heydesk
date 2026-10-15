# HeyDesk

An AI front desk a small business can set up in minutes. It picks its kind of business (a
restaurant, a clinic, a receptionist for an office, an online shop), fills in its menu, doctors
or products, hours and FAQ, and adds its own notes. Customers talk to the agent by web chat, by
voice, on Telegram or on Facebook Messenger. It answers questions, takes orders, bookings and
messages, and hands over to a person when needed.

**The model talks; code guarantees.** The model writes the words. Python decides every price,
total, date and stock count, and nothing is saved until the agent has read it back and heard a
clear yes, checked by a separate model that fails closed.

## Status

Built and tested: four desks (restaurant, clinic, receptionist, shop), web chat, voice, Telegram,
Messenger, photo search in the shop, online payments, a Google Sheet as a product list, the
owner's API and the admin panel. Each desk passed a live run on a real model. What's next is in
**[PLAN.md](PLAN.md)**. The owner's and admin's screens are a separate Vue app,
`../heydesk-frontend`.

## How it works

```mermaid
flowchart LR
    subgraph Channels
        W[Web chat<br/>SSE]
        V[Voice<br/>WebRTC]
        T[Telegram<br/>webhook]
        M[Messenger<br/>webhook]
    end
    W & V & T & M --> API[api/ · voice/<br/>routes]
    API --> S[services/<br/>limits · conversations · payments]
    S --> D[desks/<br/>prompt · tools · store]
    D --> R[runtime/<br/>turn loop · commit check]
    R --> P[ports/<br/>Protocols]
    A[adapters/<br/>Maira · Groq · Jev · Gemini · Qdrant<br/>Telegram · Messenger · SSLCommerz] -. implement .-> P
    S & D --> DB[(SQLite)]
    A --> Q[(Qdrant)]
```

- **One turn:** the visitor's message passes the limits, then the system prompt is built
  from the business's current config. The model may call the desk's tools. A tool checks facts
  in Python, and a writing tool only saves after a read-back and a yes. Every turn is saved with
  its tokens, time and cost (never the customer's words) for the admin.
- **Desks:** each kind of business is one folder in `src/heydesk/desks/` with its own prompt,
  tools, tables and routes. A new kind is a new folder.
- **Layers:** imports point inwards (routes → services → desks → runtime → ports). `runtime/`
  knows no framework, database or SDK; only `wiring.py` picks the adapters.
  `scripts/check_imports.sh` enforces this in CI.

The details are in [docs/design.md](docs/design.md), and the reason for each choice in
[docs/decisions.md](docs/decisions.md).

## Tech stack

| | |
|---|---|
| Backend | Python 3.12, FastAPI (async), pydantic v2 |
| Data | SQLAlchemy 2.0 (async) and Alembic on SQLite; Qdrant for documents and photos |
| Talking | Gigalogy's Maira (default) or Groq (`openai/gpt-oss-120b`), behind one `LanguageModel` port |
| Commit check | Jev (TypeSafe): the yes/no check before anything is saved |
| Knowledge and photos | Gemini embeddings, hybrid (meaning + exact word) search in Qdrant |
| Voice | Pipecat over WebRTC, Silero VAD, Deepgram speech-to-text, Piper speech |
| Channels and payments | Telegram Bot API, Messenger (Graph API), SSLCommerz |
| Tooling | uv, pytest, ruff, mypy (strict on the core), GitHub Actions |

## Security

- **Logins:** passwords are hashed with argon2. Login tokens are signed (JWT) and expire after
  7 days. In production the server refuses to start with an empty `JWT_SECRET` or a fake provider.
- **Tenants:** every table and query carries `workspace_id`, and tests check that one business
  can't see another's data.
- **Admins:** admins are made only from the command line, and the database confirms the role on
  every request. Owners get a 404 on admin routes. Every admin change goes to an insert-only
  audit log.
- **Abuse:** login allows 10 tries in 15 minutes per address and email. Chat allows 6 messages a
  minute per visitor and 60 per IP. Each visitor and each business has a daily cap, and a visitor
  who keeps going off-topic is blocked.
- **Webhooks and payments:** Telegram posts must carry the bot's own random secret, and
  Messenger posts must carry Facebook's HMAC signature. A payment counts only after SSLCommerz's
  own API confirms it.
- **The model:** nothing is saved without a read-back and a yes, checked by Jev, which fails
  closed. Passages from documents are given to the model as facts, never as instructions.
- **Data:**
  - Logs hold ids only, never message text, names or phone numbers.
  - Uploads are capped at 10 MB.
  - Keys live in `.env`, which is git-ignored.
- **Known gaps (next, [PLAN.md](PLAN.md) day C2):** the tokens and store passwords businesses
  give us are stored as given, and signup has no rate limit.

## Running it

You need [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv.

```bash
cp .env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn heydesk.main:app --reload
```

Open <http://localhost:8000> to chat with the dev agent, or press **Call** to talk to it
(Chrome allows the microphone on `localhost`).

Or let `scripts/dev.sh` do it. On its own it shows a menu: the backend, the backend with the
frontend (`../heydesk-frontend`), a public link, stop, status, logs and tests.
`scripts/dev.sh help` lists the short forms, such as `scripts/dev.sh a --fake --desk shop`.

In `.env`, pick the providers:

- **Talking:** `LLM_PROVIDER=gigalogy` with the three `GIGALOGY_*` keys (see
  [docs/gigalogy.md](docs/gigalogy.md)), or `LLM_PROVIDER=groq` with `GROQ_API_KEY`. `fake` runs
  without keys but only says canned lines.
- **The commit check:** `DECIDER=jev` with `TYPESAFE_API_KEY`. With `fake`, no order or message
  is ever saved: it always says no.
- **Documents and photos:** `GEMINI_API_KEY` for embeddings, and Qdrant for search. Set
  `QDRANT_URL` and `QDRANT_API_KEY` from a Qdrant Cloud cluster, or leave the URL empty to use a
  local folder. `EMBEDDER=fake` runs offline but only matches words.
- **Voice:** Deepgram for speech-to-text (`DEEPGRAM_API_KEY`; `STT_PROVIDER=chorus` uses
  Gigalogy's Chorus instead) and Piper for speech, which runs locally.

## Tests and checks

```bash
scripts/check.sh   # tests, lint, format, types, import rules: what CI runs
```

The tests are offline and free: fake models, an in-memory database and Qdrant's in-memory mode.
`uv run pytest -m live` calls the real providers and costs quota.

## Switching the kind of business

The dev agent is one desk (`restaurant`, `clinic`, `receptionist` or `shop`) and a seed file.
For the shop, set these in `.env` and restart:

```bash
DEV_AGENT_KIND=shop
DEV_AGENT_SEED=examples/agents/shop.yaml
```

Edit the seed (`examples/agents/*.yaml`) and restart to change the dev agent. Any agent can be
opened on the dev page with `http://localhost:8000/?key=<public_key>`.

## Using the API

Swagger is at <http://localhost:8000/docs>. Everything that belongs to a business needs a login
token:

```bash
uv run heydesk create-user you@example.com "a long password"   # an owner of the dev business
uv run heydesk create-admin you@example.com "a long password"  # a platform admin (no business)
```

Each prints a token. In Swagger, press **Authorize** and paste it. Forgot a password?
`uv run heydesk reset-password you@example.com` prints a new one, and
`scripts/dev.sh admin` manages admins from a menu.

| | |
|---|---|
| `POST /api/auth/signup` | A new business and its owner; it never sees another business's data |
| `GET /api/desks` | The kinds of business, and the fields each one's agent has |
| `POST /api/agent` | Create your agent: `{"kind": "restaurant"}` is enough. `GET /api/agent` then lists what is `missing` |
| `PATCH /api/agent` | Edit it: `{"faq": "…"}`, or `{"notes": "Be formal."}` for tone and rules. Live on the next message |
| `POST /api/agent/documents` | Add text, or a `.txt`, `.md` or `.pdf` (`/file`), to the agent's knowledge |
| `GET/PUT /api/menu` · `/api/doctors` · `/api/products` | A desk's list, replaced whole; each also takes a CSV at `…/import` |
| `GET /api/orders` · `/api/bookings` · `/api/appointments` · `/api/shop/orders` · `/api/messages` | What the agent took, newest first. `PATCH …/{id}` with `{"status": …}` moves one along its flow |
| `GET /api/conversations/{id}` | What was said |
| `PUT /api/agent/telegram` · `/api/agent/messenger` | Put the agent on Telegram or Messenger (needs `PUBLIC_URL`) |
| `GET /api/admin/…` | Admins only: businesses, the overview, failed turns. Act inside a business with an `X-Business: <id>` header |

Errors always look like `{"error": {"code": "…", "message": "…"}}`, with `fields` when a field is
wrong.

## Telegram

1. In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Put your ngrok domain in `.env` as `PUBLIC_URL=https://….ngrok-free.app`, and start
   everything with `scripts/dev.sh public`.
3. In the frontend, open **Your agent** → **Where customers reach you** → **Set up Telegram**,
   and paste BotFather's reply (or `PUT /api/agent/telegram` with `{"token": "…"}`).
4. Open the `link` it returns and press **Start**.

## Voice latency

The time from the caller's last word to the agent's first sound. Keep the server's log, make
about ten calls, then run the report:

```bash
uv run uvicorn heydesk.main:app 2>&1 | tee voice.log
uv run python scripts/latency.py voice.log
```
