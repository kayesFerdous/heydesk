# HeyDesk — the plan

What's built, what's next, and how to know each step is done. How it works is in
[docs/design.md](docs/design.md); why, in [docs/decisions.md](docs/decisions.md).

> **Now:** the product is built through D73 (15 Oct): four desks, web chat, voice, Telegram,
> Messenger, payments, the owner's API and the admin panel's phases 1–2. **Cleanup before the
> presentation:** day C1 (tidy the repo and docs) done 15 Oct, `scripts/check.sh` green; next is
> C2 (security), then C3 (Docker and CI).
> **Dates:** started Wed 30 Sep 2026 · demo Mon 26 Oct.

**How to use this file:** do the next unticked day. Its bullets say what to build;
design.md has the details. Tick a box only when its **Done when** passes (tests green, or
you saw it work), then update **Now**. Keep each day to the normal path (design §12).

## Built

| When | What | Decisions |
|---|---|---|
| 30 Sep – 2 Oct | The core: settings, database, JSON logs, CI; the model and Decider ports with fakes; the tool loop; the commit check (`Confirmed[T]`, Jev); web chat over SSE; voice over WebRTC (Pipecat) with spoken numbers and barge-in; the API with login | D1–D21 |
| 4 Oct | One agent per business; owner and admin roles with an audit log; the knowledge base (Gemini embeddings, hybrid search in Qdrant) | D22–D36 |
| 6 Oct | Desks: one folder per kind of business (restaurant, clinic, receptionist), replacing presets; closed days are facts | D37–D41 |
| 7–8 Oct | Telegram as a channel; the owner's alerts and takeover on Telegram; the online shop desk | D42–D45 |
| 9–11 Oct | Paged lists; paying first through each business's own SSLCommerz store; CSV import for every desk; daily limits and rate limits on the public chat | D46–D52 |
| 13 Oct | AI column matching for imports; a smarter shop agent; track, change or exchange an order; stock per size and colour; finding a product by photo | D53–D57 |
| 14 Oct | A desk's list from a linked Google Sheet; one product from rows per size; Facebook Messenger | D58–D61 |
| 15 Oct | Photos on Messenger and Telegram; restaurant table bookings and order news in the chat; admins from the command line; changing your own password; the admin panel (open a business, its page, suspend with a reason, usage, cost and failed turns) | D62–D73 |

Each desk passed a live run on Maira with Jev (13 Oct): nothing was saved without a read-back and a yes.

## Next: cleanup before the presentation

### Day C1: tidy the repo and docs

- [x] `scripts/check.sh` green again; presentation material (`proposal_uml/`, PDFs) kept out of git
- [x] Stale bits: `.env.example` lists every setting; design.md's layout matches the folders;
      strict mypy covers `desks/` (the tools moved there)
- [x] decisions.md: a replaced or changed decision says so at the start of its row
- [x] PLAN.md: this short roadmap (the long day-by-day version is in git history)
- [x] README: what it is, the architecture, the stack and the security, with correct facts
- [x] docs/demo.md: the presentation's demo, on the frontend

**Done when:** `scripts/check.sh` is green, and the README alone says what HeyDesk is, how it's
built and how to run it. [x] (15 Oct)

### Day C2: security fixes

- [ ] Signup checks the email before it makes the business, so a taken email leaves nothing behind
- [ ] Signup is rate-limited per IP, like login (D52)
- [ ] The secrets businesses give us (Telegram bot tokens, Messenger Page tokens, SSLCommerz
      store passwords) are encrypted in the database with a key from `.env`; production refuses
      an empty key; a migration encrypts the rows already there
- [ ] design §12: changing a password doesn't end logins already made (a token lasts 7 days)

**Done when:** `scripts/check.sh` is green; tests show a taken email leaves no business, a fast
signup gets a 429, and a stored token isn't readable in the table, while Telegram still works.

### Day C3: Docker and CI

- [ ] A `Dockerfile` (uv, a non-root user, migrations on start) and `compose.yaml` with Qdrant
- [ ] CI: uv's cache, older runs cancelled, `pip-audit` on the dependencies, the image built
- [ ] README: running it with Docker

**Done when:** `docker compose up` serves `/health` and the dev chat with the fake model, and CI
is green on GitHub.

## Still open

Live checks that need a real account, a device or a person:

- [ ] Telegram: an order placed in a real bot is saved only after a yes (D42); the owner's reply
      to an alert reaches a web and a Telegram visitor (D43); a product's photo gets "is it this
      one?" (D63)
- [ ] Messenger: a real Page answers, and a screenshot of a product gets its card (D60, D62).
      Needs a Meta app, its secret and verify token in `.env`, a Page token and a tunnel
- [ ] Voice: the filler is heard while an order is saved; talking over it stops it; P50/P95 of
      the first sound in the README (`scripts/latency.py`)
- [ ] Demo: two clean rehearsals in a row, and a backup video

In `../heydesk-frontend`:

- [ ] A bookings screen, the booking fields and the delivery statuses (D64–D66)
- [ ] The owner's alerts link next to the Telegram bot; the chat page shows the team's replies (D43)

## Later

- **Admin panel, phase 3 (abuse and channel health):** blocked visitors with an unblock; the
  last error of each Telegram or Messenger channel and linked sheet, on the business's page.
- **Before real customers:** Postgres; "Forgot password?" by email; two-factor login and shorter
  sessions for admins.
- **More sources for a shop's list:** WooCommerce after Google Sheets (D58).

## History

Every day's bullets and checks, from day 1 to admin day B3, are in this file's git history
(`git show 642dc26:PLAN.md`), and each change is a commit naming its decision.
