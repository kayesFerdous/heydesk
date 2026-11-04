# Project explorer index

[`docs/project-explorer.html`](project-explorer.html) is one self-contained page (Cytoscape and Mermaid
from a CDN). Open it in a browser. The views render from JSON blocks
(`<script type="application/json" id="data-*">`), so most updates mean editing JSON, not UI code.

## Editing a section without reading the whole file

```bash
grep -n "SECTION:<id>" docs/project-explorer.html
```

That prints the `START` and `END` lines. Read only that range, edit it, and keep the markers.
Inside a data block, jump straight to an item by its id, for example:

```bash
grep -n '"id": "t-shop-place_order"' docs/project-explorer.html
```

## Sections

| Section ID | What it contains | Source files it reflects |
|---|---|---|
| `view-styles` | CSS: light/dark tokens, node-type colours, prompt-owner colours | — |
| `view-shell-markup` | Header, tabs, theme button, quiz banner, one `<section>` per view | — |
| `data-modules` | `types` (graph layers), `nodes` (every module, port, adapter, external service, storage), `edges` | `src/heydesk/main.py`, `wiring.py`, `config.py`, `db.py`, `errors.py`, `logs.py`, `cli.py`, `api/`, `services/`, `runtime/`, `domain/`, `ports/`, `adapters/`, `voice/`, `shop/base.py`, `shop/csv_import.py`, `shop/districts.py`, `shop/api.py`, `web/static/`, `tts_server/` |
| `data-agents` | Agents (the shop, commit judge), their config switches, tables, and each agent→tool edge with when / why / condition | `src/heydesk/shop/__init__.py` (`tools_for`), `shop/values.py` (config), `runtime/guards.py` |
| `data-tools` | Every tool: id, desk, file, function, inputs (from its pydantic model), outputs, side effects, web label, voice filler, timeout, the description the model reads | `src/heydesk/shop/shared.py`, `shop/tools.py`, `runtime/tool.py` |
| `data-prompts` | Simulator `controls`, prompt `owners`, and every prompt as fragments (`when` conditions, `vars`, verbatim `text`): system prompt, commit check, call opening, Maira profile, tool hints | `src/heydesk/runtime/prompt.py`, `shop/__init__.py` (ROLE, `shop_prompt()`, `shop_note()`), `shop/shared.py`, `shop/values.py` (`photo_line`, `CallSettings`), `runtime/guards.py`, `adapters/llm/gigalogy.py`, `adapters/llm/groq.py`, `runtime/loop.py` (`opening_messages`), `docs/prompt-rules.md` §0 |
| `data-triggers` | Flowcharts (`charts`): nodes with shape, condition `code`, file, function; edges with labels. Message arrives, inside a turn, writing tool, after a turn, chat-app message, background loops, confirmation call, payment, auth scope, settings flags, barge-in | `src/heydesk/services/conversations.py`, `runtime/loop.py`, `runtime/guards.py`, `runtime/tool.py`, `services/visitors.py`, `services/channels.py`, `services/team.py`, `services/sheets.py`, `services/woo.py`, `services/calls.py`, `services/payments.py`, `api/payments.py`, `api/deps.py`, `services/users.py`, `config.py`, `wiring.py`, `api/__init__.py`, `voice/call.py`, `main.py` |
| `data-flows` | Step-through flows: each step `from` → `to` node, the `data` that moves, `file`, `fn`, `note` | `src/heydesk/api/chat.py`, `api/sse.py`, `services/conversations.py`, `runtime/`, `adapters/llm/`, `shop/tools.py`, `shop/store.py`, `api/telegram.py`, `services/channels.py`, `services/team.py`, `api/voice.py`, `voice/`, `services/calls.py`, `api/payments.py`, `services/payments.py`, `services/photos.py`, `api/documents.py`, `services/knowledge.py`, `services/extract.py`, `services/sheets.py`, `services/woo.py`, `api/auth.py`, `services/users.py`, `api/agents.py`, `services/agents.py`, `web/static/` |
| `data-state` | `places` (where each piece of state lives, who writes it, how it syncs, what happens on failure) and `timelines` (sequence diagrams with alt / loop / opt and failure arrows) | `src/heydesk/models/`, `shop/models.py`, `services/conversations.py`, `services/team.py`, `services/payments.py`, `services/visitors.py`, `services/rate_limits.py`, `services/knowledge.py`, `services/photos.py`, `services/sheets.py`, `services/woo.py`, `services/calls.py`, `services/channels.py`, `services/users.py`, `runtime/guards.py` |
| `data-solutions` | "How I solved it" cards: 3 first-person lines, a Mermaid mini diagram, the map `nodes` to highlight, files, an optional flow | the files named in each card |
| `data-quiz` | Mentor questions: question, short answer, and the view + argument each one opens | — (points into the other sections) |
| `data-findings` | Broken, dead or inconsistent code found while reading, with severity and files | the files named in each finding; `docs/design.md` |
| `data-agent-flow` | The guided walk through one message: `stations` and `words`, `doors` (entry points), `stages` (every parameter and its source), `gates` (checks and limits), `trace` (every call in order), `replay` (a three-turn order), `toolLife` / `toolResult` / `toolTables`, `dbRows` (table × moment), `er`, `locks` | `src/heydesk/api/chat.py`, `api/sse.py`, `main.py`, `wiring.py`, `config.py`, `services/conversations.py`, `services/agents.py`, `services/visitors.py`, `services/rate_limits.py`, `services/channels.py`, `services/calls.py`, `services/photos.py`, `services/billing.py`, `voice/call.py`, `runtime/`, `ports/llm.py`, `adapters/llm/`, `shop/`, `models/` |
| `view-how-it-works` | "How the agent works" tab (opens first): eight chapters, each one method on the view; clickable flowchart, source highlighter, step filter, replay player, table × moment matrix | renders `data-agent-flow`, and `data-tools` for the tool table |
| `view-shell` | Helpers (`DATA`, `NODES`, `fmt`, `file`, `uv`), router (`go`, `route`, pushState), theme, Mermaid drawing, shared Cytoscape style | — |
| `view-system-map` | System map: Cytoscape graph of every node, type filters, search, hover highlight, side panel | renders `data-modules`, `data-agents`, `data-tools` |
| `view-flow-player` | Data flow player: Play / Next / Back over a flow's own graph, captions | renders `data-flows` |
| `view-agent-tools` | Agent ↔ tool network: clickable edges (when / why), tool details | renders `data-agents`, `data-tools` |
| `view-prompt-simulator` | Prompt simulator: controls, stacked fragments lit or left out, final order, variables | renders `data-prompts` |
| `view-triggers` | Trigger & condition flowcharts (Mermaid); click a box for its code | renders `data-triggers` |
| `view-state-timeline` | State table and sequence diagrams (Mermaid) | renders `data-state` |
| `view-solutions` | Solution cards with diagrams; "Show on map" highlights nodes | renders `data-solutions` |
| `view-quiz` | Quiz cards; a click opens the answering view with a banner | renders `data-quiz` |
| `view-findings` | Findings table and every item marked UNVERIFIED (collected from all data) | renders `data-findings` + every `"unverified"` field |
| `view-boot` | Builds the tabs, applies the saved theme, starts the router | — |

## Data conventions

- Node ids: `c-` client, `a-` API, `s-` service, `ag-` agent, `t-` tool (`t-<desk>-<name>`, shared: `t-<name>`),
  `r-` runtime, `d-` domain, `k-` shop shared code, `v-` voice, `w-` wiring/settings/db/errors,
  `p-` port, `ad-` adapter, `x-` external, `st-` storage. Edges and flow steps refer to these ids.
- `"unverified": "why"` on any node, prompt, fragment, flow or state place shows a dashed outline,
  an UNVERIFIED badge, and a row on the Findings tab.
- A prompt fragment's `when` is `{control: value | [values]}`, all of which must hold; `"$any": [{…}, {…}]`
  holds when one of its objects holds. Controls are in `data-prompts.controls`.
- Text fields accept `` `code` `` and `**bold**`.
- Keep `</` written as `<\/` inside the JSON blocks, so a `</script>` in text can't end the block.
