# Design language: what the competitors look like, and what HeyDesk takes

Looked at on 22 Oct 2026, in a browser at 1440×900, for the frontend redesign before the
15 Nov presentation. Screenshots are in the session notes, not the repo. The frontend's own
rules (DESIGN.md, PRODUCT.md in `../heydesk-frontend`) still hold where this file is silent.

## What each one does

| | Type | Colour | Hero and how it shows the product | Trust | Bangla |
|---|---|---|---|---|---|
| **Sellzy** | Space Grotesk 800 headlines (64px, caps on "2AM ORDER"), DM Sans body, JetBrains Mono labels | Near-black navy `#0B0F1E`, electric blue, glow and gradient text | "Never lose a 2AM order again" beside a fake chat card with a clock; then six pain cards, feature cards, "not a mockup" screenshots | "2,500+ merchants", five stars, a logo strip of shops | EN / বাংলা switch in the nav, Noto Sans Bengali |
| **LazyChat** | Space Grotesk 60px, Inter body | Black, purple button, rainbow gradient on the channel names | Headline over a large dark screenshot of the inbox | Case studies, "customers loving us" | None on the first screen |
| **ARA** (SysSolution) | Inter 72px bold, Hind Siliguri for Bangla | White, navy hero band, orange `#F07830` buttons | "Never miss a customer conversation" on a dark band; product shown as a video and cards | A comparison table against "foreign AI tools" and "generic local chatbots", "teams using ARA" | Says "Bengali-native" and loads Hind Siliguri |
| **ChatBD** | System UI 60px bold, blue gradient on half the headline | White, blue, pastel badges | Centred headline, pill badges above and below, avatar row "trusted by" | A table against Wati and Twilio, "21 production-ready features" | None |
| **Revora** (was MyAlice) | Reckless serif 56px at weight 400, Roobert body, Geist in the app | Warm off-white, deep bottle green, one lime accent | A real photograph of a clothing shop behind the headline; product shown in dark green panels | Customer numbers, brand logos | None on the first screen |
| **Intercom** | Saans 80px at weight 400, tight tracking | Warm off-white `#FAF9F6`, black buttons with 4px corners | Large quiet headline, small real photos at the edges, then the product | A wall of logos | n/a |
| **Linear** | Inter Variable 64px at 510, tracking −1.4px | Near-black, grey ink, one accent used rarely | The app itself, cropped, is the picture | Logos, then craft | n/a |
| **Stripe** | Söhne 48px at weight 300 | White, one violet, a single colour field | Statement plus one action; the product shown as real UI fragments | Logos, numbers | n/a |

## What the BD market looks like as a whole

Four of the five local sites share one look: a dark or blue hero, a big bold grotesk, gradient
text, glow, pill badges, a grid of icon cards, and big round numbers ("2,500+", "21 features").
They read as busy and a little loud, and they look like each other. Only Revora looks premium,
and it does it by doing less: a serif at regular weight, a warm ground, one dark colour, a real
photograph. The global references (Intercom, Linear, Stripe) agree with Revora: large type at a
normal weight, a calm ground, one accent, and the real product as the picture.

Bangla is mostly a language switch. ARA is the only one that sets Bangla type with care
(Hind Siliguri). Nobody shows a real Bangla or Banglish conversation as the first thing you see,
which is the thing a Dhaka shop owner checks first.

## What HeyDesk takes

1. **Quiet type at a large size.** Geist at 500 with tight tracking for headlines, regular
   weight for body, like Intercom and Linear. Premium comes from size and space, not weight.
2. **The real product as the picture.** Every visual on the landing page is a working piece of
   HeyDesk built in HTML: the chat, the read-back, the order landing in the inbox. Linear and
   Stripe do this; it also shows we have nothing to hide.
3. **A real Banglish conversation first.** "L size white ache?" in the hero. No competitor
   leads with it.
4. **One calm ground and one accent.** A white sheet, a blue-grey frame, Gigalogy's cobalt only
   on the action and the selection. Dark mode for the console, from the same tokens.
5. **Trust from the mechanism, not from numbers.** We have no customers to count yet. Our proof
   is how it works: Python works out every price, the order is read back, nothing is saved
   without a yes, and a second model checks that yes. ARA's comparison table is the one trust
   device worth borrowing, done honestly.
6. **The console's frame from the premium apps**: one tinted L of sidebar and top bar, the work
   on a white sheet with a 1px edge and no shadow (Linear, Vercel, Stripe's dashboard).
7. **Bangla type taken seriously**: Noto Sans Bengali with normal tracking and taller lines,
   Bangla digits in Bangla mode.

## What HeyDesk avoids, and why

- **Gradient text, glow, neon on black** (Sellzy, LazyChat, ChatBD): the house look of the
  market. Copying it makes us the sixth of the same.
- **Pill badges above headlines, icon-card grids, "21 features"**: they say "template".
- **Invented proof** ("2,500+ merchants", five stars, logo strips): we have none, and PRODUCT.md
  forbids making any up.
- **ALL CAPS and monospace labels** as decoration.
- **A serif display face**, though Revora wears it well: Bangla has no serif that pairs with
  it on the same line, and the console must read the same in both languages.
- **The v1 look the owner rejected** (stone canvas, pill nav, green accent).
