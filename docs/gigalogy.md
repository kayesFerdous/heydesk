# Gigalogy (Maira) keys

How to get the three Gigalogy values for `.env`, and what to check when Maira
says `401 Project credential not found`.

## The three values

| `.env` | What it is | Looks like | Sent as |
|---|---|---|---|
| `GIGALOGY_PROJECT_KEY` | The credential's **project key** | ~44 characters, letters and digits (**not** a UUID) | header `project-key` |
| `GIGALOGY_API_KEY` | The credential's **API key** | ~140 characters, starts `gAAA` | header `api-key` |
| `GIGALOGY_PROFILE_ID` | Which Maira profile answers (ours: `frontdesk`) | a UUID | `gpt_profile_id` in the body |

The project key and the API key are **one pair**: they come from the same
credential, at the same moment. Never mix values from two credentials.

## Getting a credential

1. Open the Maira Console → **My Projects** → your project → the **ACCESS** tab.
2. Under **Credentials**, click the blue **+**.
3. Name it, choose the type **"Project key and API key"**, and leave the
   whitelisted domain empty (we call from a server).
4. **Download the JSON file.** It has `project_key` and `api_key`. This is the
   only time Gigalogy shows them; it keeps no copy.
5. Copy `project_key` → `GIGALOGY_PROJECT_KEY` and `api_key` → `GIGALOGY_API_KEY`.
6. Store the JSON somewhere safe (a password manager), not in the repo or Downloads.

Lost a key? Create a new credential and **stop** the old one in the Actions column.

The profile ID: the console's profile page, or the profiles list (below). A
project also has a *default* profile, used when no `gpt_profile_id` is sent;
set it to `frontdesk` in the console.

## Traps we fell into (1–2 Oct 2026)

- **The project ID is not the project key.** Both appear in the console; the
  project ID is a UUID and is in the downloaded file's *name*
  (`test-<project id>.json`). We had it in `GIGALOGY_PROJECT_KEY` for days.
- **The credential's short ID in the table** (like `a512693b`) is not a key either.
- **The profile ID is not a key.** It only picks the profile.
- If a wrong pair seems to work once, don't trust it: check the values against
  the downloaded JSON.
- Host: `https://api.maira.gigalogy.com/client`. The docs also mention
  `api.recommender.gigalogy.com`; our keys don't work there.

## Check the keys in 10 seconds

```bash
uv run python -c "
import httpx; from heydesk.config import Settings; s = Settings()
r = httpx.get('https://api.maira.gigalogy.com/client/v1/gpt/profiles',
    headers={'project-key': s.GIGALOGY_PROJECT_KEY, 'api-key': s.GIGALOGY_API_KEY}, timeout=30)
print(r.status_code, r.text[:150])"
```

- `200` with a list of profiles: the keys are right; check that your
  `GIGALOGY_PROFILE_ID` is in the list.
- `401 Project credential not found`: wrong project key, wrong API key, or the
  two are from different credentials. Compare with the downloaded JSON; if it's
  lost, make a new credential.
- `400 Profile Not Found!` on an ask: the keys are fine, the profile ID is wrong.

Until Gigalogy works, `LLM_PROVIDER=groq` runs everything the same way.
