---
name: nano-ember-ui
description: Nano's design system. Use whenever editing the Nano frontend, Electron visual shell, voice overlay, branding, settings UX, responsive layout, animations, accessibility or user-facing PC Control presentation.
---

# Nano UI

The skill keeps the `nano-ember-ui` identifier for compatibility. "Ember" was
the previous near-black-and-red system; it was replaced for 0.1.0-beta.1 and
nothing below describes it any more.

## Identity

A warm desktop workspace, not a dark console:

- cream / soft off-white reading surfaces on a slightly deeper warm ground
- one charcoal conversation rail anchoring the light workspace
- a restrained terracotta brand accent, spent on very few things
- a distinct crimson for danger — brand and destructive are never the same hue
- the supplied mark and wordmark, never redrawn or recoloured
- restrained borders and shadows; no glass, no glow, no ambient wash
- calm desktop-product hierarchy carried by type, weight and whitespace

The saturated flame red `#F40101` appears in exactly one place: the artwork in
`frontend/public/branding/`. It is never an interface colour. On light surfaces
the wordmark is filtered, not replaced.

Nano should read as calm, premium, precise and quietly futuristic — not as a
gaming UI, a generic chatbot template, a developer dashboard or a website
stretched into Electron.

## Token architecture

Colour is a role, never a literal. Components reference `--surface-hover`, not
a wash; `--text-muted`, not a grey. That is what lets the dark theme and the
charcoal rail redefine tokens and have every component follow.

- surfaces: `--bg`, `--surface`, `--surface-raised`, `--surface-hover`, `--surface-active`, `--bg-sunken`
- rail: `--sidebar`, `--sidebar-hover`, `--sidebar-active`
- borders: `--border`, `--border-strong`, `--border-accent`
- text: `--text-primary`, `--text-secondary`, `--text-muted`, `--text-dim` — four roles, four distinct values, never aliased to each other
- brand: `--accent`, `--accent-hover`, `--accent-ink`, `--accent-wash`, `--accent-faint`, `--accent-veil`
- state: `--danger`, `--warning`, `--success`, `--information`, each with a `-soft` tint
- focus: `--focus`, a warm burnt umber — deliberately not a steel blue, which
  reads as the one foreign colour on this palette

Do not hardcode a colour, radius, spacing or font size in a component rule.
`--surface-overlay` is the scrim a modal paints *behind* itself; it is not a
panel background. `:root` holds the light theme, `:root[data-theme="dark"]`
redefines values only, and `.rail` redefines its tokens locally so everything
inside the charcoal region works without rail-specific rules.

## Layout

- floating rounded top navigation, five sections
- conversation rail on the left; no fixed right inspector
- technical detail lives on its own pages, not in the main view
- desktop-first; every flexible track uses `minmax(0, 1fr)` and every scroll
  container sets `min-width: 0`
- the composer stays visible and separated from the edges, except during the
  first-run guide, which owns the whole stage

## Conversation

Assistant turns have **no bubble** and sit flush against the reading column.
User turns do have one, in `--accent-wash`. That asymmetry is the whole
mechanism for telling them apart — do not give Nano a bubble to "balance" it.
Errors use `--danger` and `--danger-soft`, never the brand accent.

## Composer

Compact and capable: one hairline border, a single warm focus ring rather than
a halo, a 36px quiet mic and a 36px accent send button. It must not dominate an
empty chat at 620px tall. An open microphone is a privacy state and gets a full
ring in `--information`, alongside the filled mic button and the wave indicator.

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

## Settings

Hierarchy comes from type size, weight and whitespace — not from nesting a card
inside a card inside a card. A nested panel is transparent. Keep the seven
categories: Geral, IA, Voz, PC Control, Memória, Privacidade, Sobre. Never hide
a security-sensitive control, and never render an architectural guarantee as a
switch.

## Motion

Restrained transitions, a travelling active-navigation indicator, meaningful
hover and focus. Respect `prefers-reduced-motion`. Never fake audio amplitude
or progress.

## Accessibility

- every text role clears 4.5:1 against every surface it can sit on, in both
  themes and inside the rail
- verify by measuring the painted page with `getComputedStyle`, walking each
  text node to its first opaque ancestor — not by reading the stylesheet
- visible focus on every focusable control, one ring colour app-wide
- hover must change a painted pixel: a white wash at 5% is invisible on cream
- keep focus traps, labels, long-text behaviour and control hit targets

## PC Control

Confirmations must clearly show ACTION, TARGET and SCOPE where relevant, at
every window size down to 940x620.

## Validating a visual change

Run the frontend typecheck and build, the Electron tests, and the Chromium
render harnesses in `electron/test/` — `render-check.js` for overflow at
1280x720, 1366x768, 1440x900, 1920x1080 and 940x620, plus `settings-drive.js`,
`chat-drive.js`, `memory-render.js` and `focus-trap-render.js`. Reading the CSS
is not validation: the muddy popover, the invisible hovers and the black blob
in the About logo were all found only by running the real application.
