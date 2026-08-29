---
name: nano-ember-ui
description: Nano Ember design system. Use whenever editing the Nano frontend, Electron visual shell, voice overlay, branding, settings UX, responsive layout, animations, accessibility or user-facing PC Control presentation.
---

# Nano Ember UI

Approved identity:
- near-black / warm-black base
- Nano red as primary accent
- official Nano mark and wordmark
- restrained glass/blur and thin borders
- red used as emphasis, not a full-screen wash
- no return to cyan/teal/blue as the main brand
- avoid excessive gaming/RGB effects

Current layout:
- floating rounded top navigation
- conversation rail on the left
- main content area
- no fixed right inspector
- technical details hidden until needed
- desktop-first responsive behavior
- composer remains visible and separated from edges

Motion:
- restrained transitions
- active navigation indicator
- meaningful hover/focus
- respect prefers-reduced-motion
- never fake audio amplitude/progress

PC Control confirmations should clearly show ACTION, TARGET and SCOPE where relevant.

Settings should stay organized around Geral, IA, Voz, PC Control, Memória, Privacidade and Sobre.

For meaningful visual changes run frontend typecheck/build, Electron tests and render/layout checks.
