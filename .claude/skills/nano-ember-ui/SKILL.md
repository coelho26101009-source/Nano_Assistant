---
name: nano-ember-ui
description: Nano's design system. Use whenever editing the Nano frontend, Electron visual shell, voice overlay, branding, settings UX, responsive layout, animations, accessibility or user-facing PC Control presentation.
---

# NANO UI

The skill keeps the `nano-ember-ui` identifier for compatibility. "Ember" was
the near-black-and-red system, replaced by a warm cream/terracotta one for
0.1.0-beta.1, which was in turn replaced by the direction below. Nothing here
describes either of them any more.

## Identity

The visible product name is simply **NANO** — never "Nano Assistant", "Nano AI"
or "Nano Brain" as the product name. The interface follows the design language
of [Chatbot UI](https://github.com/mckaywrigley/chatbot-ui): neutral, quiet,
comfortable for hours. Patterns only; no Chatbot UI code or assets are copied.

- a near-black default palette, with an optional light theme
- one accent: the soft blue of the symbol, spent on very few things
- a red for danger and an amber for warnings; neither ever borrows the blue
- use the supplied symbol and organic wordmark through `components/NanoLogo.tsx`
  and the `public/branding/*-original.png` assets; do not trace or redraw them
- restrained borders and shadows; no gradients on controls, no glow, no glass

simple > flashy · fast > animation-heavy · readable > decorative ·
consistent > different on every screen · functionality > branding.

## Token architecture

Colour is a role, never a literal. `:root` holds light values, while
`:root:not([data-theme="light"])` makes dark the default before React mounts.

- surfaces: `--bg`, `--surface`, `--surface-raised`, `--surface-hover`, `--surface-active`, `--bg-sunken`, `--msg-band`
- sidebar: `--sidebar`, `--sidebar-hover`, `--sidebar-active`
- neutral primary: `--primary`, `--primary-hover`, `--on-primary` — buttons, the send button, count badges
- accent: `--accent`, `--accent-hover`, `--accent-ink`, `--accent-wash`, `--accent-faint`, `--accent-veil`, `--on-accent`
- text: `--text-primary`, `--text-secondary`, `--text-muted`, `--text-dim` — four roles, four distinct values
- state: `--danger`, `--warning`, `--success`, `--information`, each with a `-soft` tint
- focus: `--focus` (the accent) and `--focus-ring`; one focus colour app-wide

Where the accent goes: focus, links, switches and checkboxes that are on, rows
picked for a bulk action, the graph's selection, and the mark. Where it does
NOT go: buttons, active navigation, the selected conversation, menus — those
are neutral, which is what keeps the application from turning blue. "Working"
and the open microphone use `--information` (teal), never the accent.

## Layout

- sidebar (`components/Rail.tsx`): brand, new conversation (neutral primary),
  search, the sections (Chat, Ferramentas, PC, Memória), the conversation list
  grouped by day, Definições at the foot. Expanded, it shows the N and the
  wordmark; collapsed, a 60px column of icons with the N on top; below 1024px it
  starts collapsed and opens as a drawer; below 640px it leaves the layout and
  the top bar carries the menu button
- top bar (`components/TopBar.tsx`): the open page's title, then the AI
  selector, the approvals bell and, in the desktop shell, the window controls.
  It and the sidebar's brand row are the window's caption (drag regions)
- the navigation model is data (`lib/navigation.ts`), rendered by both
- multi-view sections show their views as a tab list above the page
- every flexible track uses `minmax(0, 1fr)`; every scroll container sets `min-width: 0`
- the frontend uses no browser storage of any kind (a security test holds it)

## Home

The mark, "Como posso ajudar?", the composer, and a few light suggestions under
it — nothing else. No dashboards, telemetry or cards on the home screen.

## Conversation

Chatbot UI's rows: every turn has a header with an avatar and a name, both
sides share one left-aligned reading column, and NANO's turns sit on a faint
full-width band (`--msg-band`). The user's avatar shows initials only when the
backend knows their name. Code blocks are dark in both themes. Errors use
`--danger` and `--danger-soft`, never the accent.

## Composer

One rounded field: the text on top, a quiet row of controls under it (the
attachment slot, disabled with its reason, the voice status, the microphone and
the neutral-primary send button). Focus turns the border the focus colour — a
border, not a ring, because the composer holds focus by default. An open
microphone is a privacy state: `--information` border, filled mic button and
the wave indicator.

## State presentation

Never let colour alone carry a state. Each differs in fill or motion too:

- ready — `--success`, filled
- working — `--information`, filled, pulsing
- waiting / fallback — `--warning`, filled
- approval required — `--warning`, filled, faster pulse
- **not configured — `--warning`, hollow ring**
- error — `--danger`, filled
- offline — `--text-dim`, filled

A missing or unconfigured provider must never render as a state that reads as
healthy. Presentation follows measured route availability, not agent idleness.
A rate limit shows its own countdown, with the provider named.

## Settings

Hierarchy comes from type size, weight and whitespace — not from nesting a card
inside a card. A nested panel is transparent. Keep the seven categories: Geral,
IA, Voz, PC Control, Memória, Privacidade, Sobre. Never hide a
security-sensitive control, and never render an architectural guarantee as a
switch.

## Motion

Short transitions on colour and background only; no looping decoration.
Respect `prefers-reduced-motion`. Never fake audio amplitude or progress.

## Accessibility

- every text role clears 4.5:1 against every surface it can sit on, in both themes
- verify by measuring the painted page with `getComputedStyle`, walking each
  text node to its first opaque ancestor — not by reading the stylesheet
- visible focus on every focusable control, one focus colour app-wide
- hover must change a painted pixel
- icon-only controls keep a text name (`aria-label`, or an `sr-only` label)
- keep focus traps, labels, long-text behaviour and control hit targets

## PC Control

Confirmations must clearly show ACTION, TARGET and SCOPE where relevant, at
every window size down to 940x620.

## Validating a visual change

Run the frontend typecheck and build, the Electron tests, and the Chromium
harnesses in `electron/test/` — `render-check.js` for overflow at 1280x720,
1366x768, 1600x900, 1920x1080 and 940x620, plus `settings-drive.js`,
`chat-drive.js`, `memory-render.js`, `focus-trap-render.js` and
`csp-check.js`. Reading the CSS is not validation: defects in this system were
repeatedly found only by running the real application and looking at it.
