# HeyDesk

An AI front desk a business can build in minutes. It picks a preset
(restaurant host, receptionist, clinic front desk), fills in its menu, hours and FAQ, edits the
instructions. Visitors talk to the agent by web chat or by
voice; it answers questions, takes orders and messages, and hands over to a
person when needed.

The model writes the words; code decides every price, total and time, and
nothing is saved without reading it back and hearing a yes.

## Status

Web chat and voice orders, the restaurant, receptionist and clinic presets, and the
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

## Switching presets

The dev agent comes from a preset and a seed file. For the receptionist (or
`clinic` with `examples/agents/clinic.yaml`), set these in `.env` and restart:

```bash
DEV_AGENT_PRESET=receptionist
DEV_AGENT_SEED=examples/agents/receptionist.yaml
```

Edit the seed (`examples/agents/*.yaml`) and restart to change the dev agent:
its name, knowledge, FAQ and items. Any agent can be opened on the dev page
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
| `GET /api/agent` | Your agent (a business has one), with its `public_key` (the chat URL) |
| `PATCH /api/agent` | Edit the config: `{"faq": "…"}`, or `{"notes": "Be formal."}` for tone and rules. Live on the next message. `instructions` is for admins |
| `PUT /api/agent/items` | Replace the items, for example a new price (in paisa) |
| `POST /api/agent/documents` | Add pasted text to the agent's knowledge: `{"title": "Policies", "text": "…"}`. `POST /api/agent/documents/file` takes a `.txt`, `.md` or `.pdf`. `GET` lists them and `DELETE /api/agent/documents/{id}` removes one. The agent searches them when a question isn't answered elsewhere |
| `POST /api/agent/items/import` | Items from a spreadsheet saved as CSV: `{"csv": "…", "mapping": {"Product Title": "name"}}`. Columns we don't know become the items' own facts. Add `"dry_run": true` to check it first |
| `POST /api/agent` | Create your agent from a preset: `{"preset": "restaurant"}` is enough (`GET /api/presets` lists them). `GET /api/agent` then lists what is `missing` |
| `GET /api/admin/businesses` | Admins only: every business. Suspend one with `PATCH /api/admin/businesses/{id}`; act inside one with an `X-Business: <id>` header on the routes here |
| `GET /api/inbox` | The sidebar: each job the agent does, and how many are waiting |
| `GET /api/records` | What the agent took, orders and messages together. Filter with `?job=`, `?status=`, `?q=` (number, name or phone) |
| `PATCH /api/records/{id}` | `{"status": "ready"}`. An order goes new → ready → collected (or cancelled); a message new → handled → archived |
| `GET /api/conversations/{id}` | What was said, and the records it made |

A new business signs up with `POST /api/auth/signup` and gets its own
workspace; it never sees another's agents. Errors always look like
`{"error": {"code": "…", "message": "…"}}`, with `fields` when a field is wrong.

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
