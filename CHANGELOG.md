# Changelog

All notable changes to Nano are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project intends to follow [Semantic Versioning](https://semver.org/)
from its first public release onward.

## About the entries below

`0.1.0-beta.1` is Nano's **first public release**. Everything under it is the
work that produced it, grouped by milestone rather than by version, because
those milestones predate the existence of any release to attach them to.

Dates are the real dates of the commits that delivered the work, taken from the
repository history. See [docs/RELEASING.md](docs/RELEASING.md) for the
versioning policy.

---

## [Unreleased]

### Changed — the island stays hidden until it is needed (2026-11-22)
- The island is no longer always on screen. It comes out for a voice turn, an
  approval, a reminder or alert, a tool at work, the volume or the song
  changing, and when the mouse is pushed against the top centre of the screen,
  then slides back up on its own. "Ilha sempre no ecrã" brings the old resting
  pill back. A song that keeps playing no longer keeps the island visible.

### Added — call NANO by voice, and it listens until you stop (2026-11-22)
- A voice turn stops recording when you stop talking (a short pause), instead
  of always recording 7 seconds. Silero VAD (inside the voice pack, nothing
  new to download) tells speech from noise; without it, the calibrated energy
  gate is used. Settings → Voz → "Como o NANO te ouve": the pause length.
- "Ei Nano" / "Hey Nano" rebuilt: NANO waits for someone to speak, cuts the
  utterance where they pause and checks only its start with a quick local
  pass, so nothing is transcribed while nobody talks. The phrase has to open
  what was said (a conversation about NANO does not wake it), and a request in
  the same breath ("Ei Nano, abre o Spotify") goes straight to the turn. Still
  off until the person turns it on; on/off also in the tray, which says when
  it is listening.
- "Qualidade do reconhecimento: Melhor" (opt-in): large-v3-turbo (~1.6 GB),
  downloaded first and used only once it is on disk; the wake check keeps
  using the small model.

---

## [0.4.0-beta.2] - 2026-11-22

"Beta 4", first update. Release notes: [docs/RELEASE_NOTES_0.4.0-beta.2.md](docs/RELEASE_NOTES_0.4.0-beta.2.md).
Installed copies of 0.4.0-beta.1 update to it by themselves.

### Changed — the island in sober colours (2026-11-21)

- The island uses a soft black (`#0C0C0D`) instead of pure black, warm
  off-white text and quiet greys. Each
  tone (info, accent, success, warning, danger) is desaturated to a hint of
  colour, so the states stay tellable apart without being vibrant; the ready
  dot is pale blue. Hard-coded colours in the overlay now go through tokens
  (`--hairline`, `--tile`, `--text-soft`).

---

## [0.4.0-beta.1] - 2026-11-21

"Beta 4". Release notes: [docs/RELEASE_NOTES_0.4.0-beta.1.md](docs/RELEASE_NOTES_0.4.0-beta.1.md).
The first Beta that updates itself.

### Added — the phone's camera, with permission each time (2026-11-21)

- **"O que é isto?"** A camera button in the phone app's chat takes one photo
  and sends it with the message.
- **NANO can ask to see** (`phone_camera_look`): the phone shows a card, and
  only a tap on "Abrir a câmara" opens it; one photo, "Recusar" or 90 s
  without an answer is a no. A red "Câmara ligada" sign while it is open.
  Nothing is recorded; the photo travels inside the encrypted link, is
  described by a vision model (a local one first) and dropped.

### Added — the Second Brain in 3D (2026-11-20)

- The memory graph is now real 3D: black space, lit spheres, a slow turn and
  smooth animations; drag to turn it, search to glide to a node. Nothing moves
  with reduced motion.

### Fixed — before the release (2026-11-21)

- The island's music reader gives up after 4 s when Windows' media controls do
  not answer (seen with Spotify playing), instead of freezing; a skip that gets
  no answer falls back to the keyboard media key. The island test no longer
  reads the real media session, so the suite no longer hangs on a PC that is
  playing music.

### Added — Memory M3: remembering past conversations (2026-11-20)

- **NANO remembers older conversations and what was decided in them.** Each
  conversation with at least two of your messages gets a recap (summary,
  decisions, topics) made only from sentences you wrote, locally and without a
  model call (`core/conversation_recall.py`, schema v4 `conversation_recaps`).
- When a new message touches the same subject, or asks "lembras-te…?" / "what
  did we decide…?", the recap of that conversation reaches the model with its
  title and date as the source, and the answer shows **Lembrei-me de «…»** with
  a chip that opens that conversation. On a question about the past, the exact
  old messages of other conversations are searched too.
- **Memória → Conversas antigas**: see, search, edit (your version is kept),
  forget (the conversation stays, NANO stops recalling it) or bring back every
  recap; "Esquecer todas". Deleting a conversation deletes its recap; "Apagar
  todos os dados" empties them with everything else. Switch: Definições →
  Memória → "Lembrar conversas antigas" (needs cross-conversation memory on).
- The `memory_search_history` tool now returns whole conversations (title, date,
  summary, decisions) before the matching messages.

### Added — deep research with a report and sources (2026-11-20)

- **"Investiga a fundo…", "pesquisa com fontes…", "faz-me um relatório
  sobre…"**: NANO searches the question from up to four angles (DuckDuckGo,
  Bing when DuckDuckGo gives nothing, and Wikipedia), reads up to eight sites
  (at most two per domain), keeps the paragraphs that answer the question and
  cross-checks them: pages found by several searches rank first, and figures
  that appear in more than one site ("8 GB", "115 W") are listed with the
  sources that agree. The report has a summary, the facts with `[n]`
  citations, where the sources disagree and a confidence line.
- **The list of sources under the report is written by NANO**, from the pages
  really read, never by the model, so a link cannot be invented. Links are
  clickable on the PC and on the phone; spoken replies skip the `[n]` marks
  and the list.
- Read-only and public only (`browser.read`, no card): every address and
  every redirect is checked, so a result cannot reach this PC or the router;
  the pages are fenced as external content. `core/research.py`,
  `plugins/research.py`, tests in `tests/test_research.py`.

### Added — the phone away from home, always encrypted (2026-11-20)

- **The phone app works away from home with nothing to set up.** The PC keeps
  one outgoing connection to NANO's relay (`relay/`, a Cloudflare Worker on the
  free plan) and the phone meets it there. No port is opened on the router and
  nothing connects in to the PC.
- **The relay cannot read anything.** It only forwards the same AES-256-GCM
  envelopes the phone sends on the home Wi-Fi, sealed with the phone's own key.
  Pairing over it still needs the yes on the PC and the same six digits on both
  screens; replay protection and removing a phone work exactly as at home.
- Settings → General → Phone → **Away from home** (on by default) shows whether
  the PC is connected. Phones paired before this learn the relay on their next
  start at home, without pairing again. Tailscale still works as an
  alternative.

### Added — the NANO island (2026-11-20)

- **The voice capsule became the NANO island**: a black pill at the top centre
  of the screen that changes shape for what NANO is doing, with smooth spring
  animations. Listening (with an elapsed clock), thinking, speaking, a tool
  being used ("A pesquisar na web…"), an approval waiting (amber; click opens
  NANO, the decision stays in NANO), reminders, PC alerts, finished downloads.
- **Modo ambiente**: at rest the island stays as a small pill, ready (hover
  shows the shortcut). Switch it off in the tray or Definições → Geral.
- **Music on the island**: the song playing on the PC (Spotify, a browser, any
  app in Windows' media controls) with cover, title, progress and previous /
  play-pause / next; the player opens for a moment when the song changes.
- **Volume on the island**: a level bar when the system volume changes.
- The tray menu now follows the interface language.
- New runtime dependency on Windows: `winrt-Windows.Media.Control` (+ runtime,
  Foundation, Storage.Streams), for the music only. Without it the island works
  and shows no music.

### Added — JARVIS P1 (2026-11-20)

- **Voice ready in one click.** Definições → Voz → "Preparar a voz" (also in the
  first-run guide) installs local speech recognition, hash-checked from the
  optional lock, and downloads its model with a progress bar. Then NANO asks
  whether to turn on the wake phrase.
- **Spoken answers start at the first sentence**, while the rest is still being
  written, instead of after the whole answer.
- **Personality.** Definições → Geral → Personalidade: tone (calm and brief with
  a little humour, JARVIS style, by default; friendly; formal; neutral), how NANO
  addresses you, answer length, personal instructions, and the speaking voice.
- **Protocols.** PC → Modos → "Os teus protocolos": your own routines started by
  a phrase ("modo estudo", "boa noite", "modo jogo"), typed, spoken or from the
  phone. Each step goes through the normal permissions. Three templates.
- **PC alerts.** Low battery, disk almost full, network down and back, download
  finished. Local only; each one is a switch in Definições → Geral → Avisos do PC.

A full review of the code: 50 bugs fixed, and a design pass against premium
chat apps.

### Added

- **Automatic updates on the PC.** The installed app checks GitHub shortly after
  it starts and every 6 hours, downloads a newer version in the background and
  installs it when you close NANO (or now, with **Restart and update** in
  Settings → About or the tray). It only accepts a version described by an
  update feed signed with the project's key, checks the installer's size and
  SHA-256 before running it, never downgrades, never installs while Windows is
  shutting down, and tells you on the next start whether the update worked.
  Run from the project folder, NANO only says that a new version exists.
  Settings → About → Updates has the switch. See `docs/UPDATES.md`.
- **Updates for the phone's test app.** The test APK checks for a newer test
  build at most twice a day and offers it in a banner; **Update** downloads it,
  checks the signed feed, the size, the SHA-256 and that it is signed with the
  same key, and opens Android's installer, which always asks you. The Play
  Store build has no updater (Google Play updates it).
- **Read aloud** under every answer, on the PC (NANO's voice, only while spoken
  replies are on) and on the phone (the phone's own voice).
- **Pairing a phone shows a 6-digit code on both screens.** The link key is now
  agreed with the phone's own key (ECDH P-256), so a photo of the QR code is
  not enough to connect another phone. A phone with the old app is asked to
  update; phones already paired keep working.

### Security

- Dependencies refreshed with no breaking changes: Electron 44.11.5,
  electron-builder 26.18.1 (its `minimatch` moved to 11.0.9 for
  GHSA-g3hv-75wg-m249, the moderate alert GitHub reported), Next.js 16.4.6,
  Capacitor 8.6.2, Vite 8.3.8, Vitest 5.1.1. `npm audit` reports 0 issues in
  frontend, electron and mobile, and `pip-audit` none in the Python locks.
- Dependabot now also watches the phone app (`mobile/`).

### Fixed

- Security: deleting a folder no longer follows links out of it; opening
  scripts and shortcuts (.py, .url, .lnk and others) asks first; web searches
  encode the query; turning phone access off also closes open phone
  connections.
- Chat turns can no longer interleave; several pending approvals are queued
  instead of the second one being hidden.
- Memory no longer stores lines like "I like this answer" as facts, and
  "remember that ..." requests are no longer mistaken for questions.
- Vision in CLOUD mode only sends the picture to the chosen provider.
- Screen watch does not fire at once for text or a window already on screen.
- All-day events are kept and shown as "All day"; a failed agenda read says
  so; monthly reminders keep their day.
- The phone survives a PC restart, never sends a message twice, and follows
  the conversation opened on the PC (and the PC follows the phone).
- Retry or edit after a message that never reached NANO resends it instead of
  deleting the previous exchange.
- NANO.bat and NANO_DESKTOP.bat stop when the interface build fails.
- More messages translated into English.

---

## [0.3.0-beta.1] - 2026-11-19

The third public Beta ("Beta 3"). Still a pre-release: the Windows installer is
unsigned, updates are manual, and the Android app is a test build installed by
hand. The numeric part moved from 0.2 to 0.3 so Windows treats it as newer than
0.2.0-beta.1 (see [docs/RELEASING.md](docs/RELEASING.md)).

### Highlights

- **English by default, Portuguese too.** The interface follows the system
  language (English when NANO does not speak it yet), with a selector in
  Settings → General. NANO answers in the language you write in.
- **Voice in English and continuous conversation.** Speech recognition, the
  voice and the wake phrase ("hey nano" / "ei nano") follow the interface
  language. After a spoken answer NANO keeps listening for a few seconds, so a
  conversation does not need the hotkey every time. Full-screen voice mode:
  Ctrl+Shift+M.
- **NANO sees.** With your permission it can describe the screen with a vision
  model, read the text on screen with Windows' own OCR, and watch the screen for
  a while ("let me know when the download finishes"). Messages accept PDFs,
  images and text files.
- **Memory that knows you.** NANO remembers facts about you without repeating
  them, refuses secrets and hearsay, and links what it knows to your agenda
  ("Maths test tomorrow" → "you said maths is hard for you").
- **NANO speaks first.** A morning briefing, a heads-up before events, and an
  offer to get ready the evening before something important. Built locally from
  your agenda; it never acts on its own.
- **Local AI, guided.** Settings → AI → "AI on your PC: guided setup" looks at your PC, recommends one light
  model that writes, sees and uses tools, and downloads it on one click.
- **Tasks of several steps.** For a request that takes three or more actions,
  NANO shows the plan once and carries it out after your yes.
- **NANO on the phone (Android).** A companion app for the NANO on your PC, with
  chat, voice, agenda and reminders, end-to-end encrypted.

### Added

- **Languages.** Every interface string has an English entry
  (`frontend/lib/locales/en.ts`, `core/locales/en.py`); `tests/test_i18n.py`
  fails on a missing one. The erase-all phrase is accepted in either language.
- **Voice follows the language** (`core/voice_language.py`): transcription
  forced to the interface language, `en-US-GuyNeural` / `pt-PT-DuarteNeural`,
  wake phrase and vocabulary hint switched live. **Continuous conversation**
  (Settings → Voice, on by default) ends on silence, "that's all" / "é tudo" or
  after 6 follow-ups. **Barge-in** (off by default) lets you interrupt NANO.
- **Full-screen voice mode** (Ctrl+Shift+M, the command palette or the waves
  button): one big control, the turn's phase as a ring, the last exchange.
- **See the screen** (`core/vision.py`, `pc_screen_describe`): off by default
  (Settings → Privacy → "See the screen with AI"), always asks, refuses password
  managers, keeps no image. In LOCAL mode only an Ollama vision model is used.
- **Watch the screen** (`core/screen_watch.py`, `pc_screen_watch`): up to 3
  watches of up to 120 minutes for a window or a piece of text that appears or
  disappears. NANO only tells you; it never acts.
- **Attachments:** up to 5 text files per message, plus PDFs (read locally with
  pypdf) and images (described by the vision model). File contents reach the
  model inside the untrusted-content fence.
- **Memory M2:** recall questions no longer become memories; English facts
  ("I'm a…", "I like…", "call me…"); secrets in natural phrasing (short
  passwords, PINs, card numbers, IBANs, more API key families) and permission
  claims are refused. 53 regression cases.
- **Memory meets the agenda** (`core/proactive.py`): between 18:00 and 23:00 NANO
  offers to help prepare for tomorrow's exam, interview, meeting or trip, with up
  to two related facts. Settings → General → "Help me get ready for tomorrow".
- **Proactivity:** a daily briefing (08:00 by default) and a warning a few
  minutes before events, in the chat and as a Windows notification. Settings →
  General → "NANO speaks first".
- **Guided local AI** (`core/local_setup.py`): reads RAM, cores, GPU memory and
  free disk, recommends one Qwen3-VL size (2b / 4b / 8b), links to Ollama when it
  is missing, and downloads only when you press the button.
- **Tasks of several steps** (`plugins/task_plan.py`): the plan is shown once;
  approving it skips only the extra "are you sure?" for the tools it listed in
  that turn. Closing apps, power, deleting and sending still ask.
- **Design review:** a three-step first run (brain, privacy, name); a personal
  home; approval cards inside the conversation; pinned chats and projects in
  the sidebar; "Open beside" for long answers and code; retry and edit on the
  last turn; a keyboard shortcuts list; an Advanced mode switch that hides
  developer pages by default; theme "Follow the system".
- **Reminders on this PC are created without a confirmation card** (they still
  ask when the same turn read outside content). Read-only tools (list reminders,
  events, facts, documents) no longer ask either.

- **NANO on the phone (Android).** A new app in `mobile/` (Capacitor + React)
  that is a remote for the NANO on your PC: the same conversations, memory and
  agenda, chat with voice (Android speech recognition and text-to-speech),
  approval cards for PC actions, reminders copied to the phone as notifications
  so they ring with the app closed, dark design matching the PC, English with
  European Portuguese. Pairing is a QR code in Settings → General → Phone plus a
  yes on the PC; every message is end-to-end sealed with AES-256-GCM
  (`core/mobile_link.py`). Away from home it works over Tailscale, with no port
  opened to the internet. The APK is attached to this release; CI also builds a
  rolling test APK (`android-test` pre-release) and a Play Store bundle
  (`.github/workflows/android.yml`).

### Fixed

- **Desktop window no longer stays black.** The window now opens at once on a
  local startup screen ("Starting NANO…") and, if the interface fails to load
  or loads without drawing anything, shows an error screen with a reason code
  instead of an empty dark window. The launcher rebuilds a frontend build that
  carries an inline script (an old Turbopack build the desktop CSP blocks) even
  when its file dates look current. In desktop mode the backend no longer exits
  when its page closes, so "Try again" reaches a live engine.
  `NANO_DISABLE_GPU=1` forces software rendering for graphics drivers that
  paint Chromium windows black.

### Changed

- **Frontend on Next.js 16** (16.4.5, from 15.5.29). The build now uses
  Turbopack; `turbopack.root` points at the repository so `lib/version.ts` can
  still read `version.json`. The removed `eslint` key is gone from
  `next.config.js`. `npm audit` reports 0 vulnerabilities. React stays on 18,
  which Next 16 still supports but deprecates; React 19 is needed before Next 17.

### Documentation

- `docs/CLEAN_WINDOWS_VALIDATION.md`: a step-by-step script for validating an
  installer on a clean Windows account, including every feature added in this
  release.
- `docs/CODE_SIGNING.md`: signing options for the installers, and the exact
  electron-builder and workflow changes to make once an account exists.

### Added (agenda, reminders, screen text and privacy)

- **Recurring reminders and everyday phrases.** Reminders understand
  "amanhã às 9", "sexta às 15h", "depois de amanhã", "9 e meia" and "3 da
  tarde", and can repeat: "todos os dias às 8", "nos dias úteis",
  "todas as segundas às 9" or "todos os meses". A recurring reminder moves to
  its next slot when it fires (claimed atomically, so it is delivered once
  per slot), and slots missed while the PC was off are skipped rather than
  fired in a burst. The Agenda page gains a "Repetir" choice and shows the
  rule beside each reminder. Old databases gain the new column on first use.
- **Read the text on screen, locally and only if you allow it.** New
  `pc_screen_read_text` uses Windows' own OCR on the active window, a window, a
  monitor or a region. It is off until Definições → Privacidade → "Ler texto
  do ecrã" is on, always asks, refuses password managers and authenticators,
  masks keys, passwords, card numbers and IBANs, returns at most 4000
  characters, keeps no image, and its output is fenced as untrusted content.
  Needs the optional pywinrt packages (added to `requirements/optional.lock`).
- Screenshots can now capture **one monitor** (`mode=monitor`) or **a region**
  (`mode=region`, clipped to the monitor), each bound to its own grant.
- **Work modes are back, safely** (PC → Modos). A mode in `config/modes/*.yaml`
  compiles into ordinary PC Control actions (close apps, open apps and sites,
  set the volume, open a Windows Settings page); the page shows every step
  before anything runs, and "Ativar" sends each one through the permission
  pipeline. Closing apps asks each time and never forces; apps that are not
  open are skipped without a prompt. The model gets read-only
  `work_mode_list` / `work_mode_plan` and applies a mode with the narrow tools.
  Mode ids are a closed allow-list of the files in the folder; terminals,
  interpreters, paths and non-http(s) URLs are refused.
- When a PC Control target is ambiguous (two windows called "relatório", two
  apps matching "Spotify"), the chat now shows the candidates as buttons under
  Nano's question. Pressing one sends a normal message naming the exact
  window or app, so it goes through policy and confirmation again. On a voice
  turn the overlay shows "Qual delas?" with the options.
- New **Agenda** section in the sidebar: see pending reminders and the
  calendar events of the next 7, 30 or 90 days (local, imported .ics and
  Google, labelled), create reminders and events with a form, and delete them
  after a confirmation. Google events are read-only. The page talks to the
  running plugins through `core/agenda.py`; nothing here is a model tool.
- Reminders now show up on the PC: Nano Desktop raises a Windows notification
  when one falls due (also with the window hidden in the tray; clicking it
  brings Nano forward), and the browser mode shows an in-app toast. The phone
  webhook still works as before. `reminders.desktop_notifications: false`
  turns the local notification off.
- "Apagar todos os dados" in Definições → Privacidade erases every
  conversation, memory, legacy fact, the user profile, the whole Second Brain,
  indexed documents and the finished-task history in one transaction, then
  compacts the database so the text does not linger in free pages. It asks for
  the typed phrase `APAGAR TUDO`, which the backend checks too, and the dialog
  lists what is kept: settings, API keys, connected accounts, permissions and
  logs. It is a Settings control only, never a model tool.

### Fixed

- "Apagar todos os dados" now also erases reminders and calendar events, which
  live in the same database and used to survive it.
- Reminders: an absolute time that has already passed is refused instead of
  firing at once; "dd/mm HH:MM" for a date already past this year means next
  year; a due reminder is claimed before delivery, so a failure part-way can no
  longer re-send it every 20 seconds.
- Calendar `.ics` import: UTC times (`...Z`) are converted to local time
  instead of being stored an hour or more off; folded long lines, `SUMMARY`/
  `LOCATION` with parameters and escaped text (`\,` `\;` `\n`) are read
  correctly; re-importing a file no longer duplicates events; imported events
  can be deleted.
- Google Calendar reading (`calendar.google_enabled`) needed two libraries that
  were not declared anywhere; `google-api-python-client` and
  `google-auth-oauthlib` are now in `requirements-optional.txt` and
  `optional.lock`.
- `core.speech_filter` no longer fails to import on Python 3.13+, where
  `audioop` was removed; it falls back to an equivalent pure-Python RMS.
- User-facing replies and tool descriptions no longer hard-code a personal
  name.
- Frontend: Next.js 15.5.26 → 15.5.29 and `source-map-js` updated (npm audit
  clean); removed the `export` script, since `next export` no longer exists
  (`next build` with `output: "export"` already produces `out/`).
- Tests no longer depend on the machine: proxy tests clear the lower-case
  `https_proxy`/`no_proxy` variables too, the untrusted-output test no longer
  needs working DNS, and an invalid escape sequence warning is gone.

---

## [0.2.0-beta.1] - 2026-09-30

The second public Beta. This remains a pre-release, with an unsigned Windows
installer and manual updates.

### Changed

- Rebuilt the desktop interface around a dark-first NANO design, with clearer
  navigation, conversation layout and responsive behavior.
- Introduced the new NANO symbol and Windows app, tray and installer branding.
  Replaced the symbol derivatives with ones generated from the supplied master
  to remove bright edge artifacts on dark backgrounds.
- Refined the Brain and Memory presentation and redesigned the external voice
  overlay. Motion is more restrained and respects reduced-motion preferences.
- Improved AUTO provider routing and bounded status waits and failover. LOCAL
  and CLOUD modes remain isolated from the opposite provider path, and changes
  to provider credentials refresh status more promptly.

### Reliability and maintenance

- Pinned Python build, runtime and test dependencies in hash-verified locks for
  reproducible installs and packaged builds.
- Removed an unused dependency, updated optional dependency ranges, upgraded
  Next.js and Electron, and added stability and security test coverage.
- Reduced startup and provider-status blocking, including stale Ollama probes
  and work on the UI bridge and audio startup path.

---

## [0.1.0-beta.1] - 2026-09-18

The first public Beta. Pre-release: not a stable version, not 1.0.

### Interface

- Rebuilt the interface around a warm cream, charcoal and terracotta design
  system. The previous system was near-black with the flame red of the mark
  spent on navigation, primary actions, focus and permission emphasis at once;
  that read as a gaming skin rather than as desktop software, and one red
  family carrying both the brand and "this is dangerous" left the two
  indistinguishable. The brand accent is now a terracotta and danger a cooler
  crimson, and they are never the same hue.
- Colour is a semantic role rather than a literal, so the dark theme and the
  charcoal conversation rail redefine tokens and every component follows.
- Four text roles replace two, fixing helper copy and metadata that rendered
  identically.
- Focus rings are warm rather than steel blue; every text role clears 4.5:1
  against every surface it can sit on, in both themes, measured off the painted
  page rather than derived from the stylesheet.
- A provider that is missing or unconfigured now shows a hollow amber ring
  instead of an indicator that read as healthy. Provider routing is unchanged.
- The first-run guide owns the whole stage instead of sharing it with the
  composer, which was the source of the cramped onboarding at 940x620.
- Assistant turns are unbubbled and flush with the reading column; user turns
  keep a bubble.
- Fixed a hand-drawn SVG "flame" on the About page whose inner path referenced
  a token that has never existed, painting a black blob inside the logo. It now
  uses the supplied mark.

### Release

- First tagged version, published as a GitHub pre-release with the NSIS
  installer and `SHA256SUMS.txt`.
- Windows binaries are **unsigned** in this Beta.
- There is no automatic updater; Beta updates are manual.
- The MSI is built and verified but not published in this release: a full
  managed-installer lifecycle has not been validated.

---

## Milestones before the first release

### Documentation truth pass — 2026-09-09

- Audited every public document against the code and corrected what had gone
  stale. `PRIVACY.md`, `README.md` and `docs/architecture/MODEL_ROUTING.md` were
  rewritten; `SECURITY.md`, `docs/SECURITY_POLICY.md`,
  `docs/architecture/ARCHITECTURE.md`, `docs/VOICE.md`, `docs/README.md`,
  `docs/architecture/PC_CONTROL.md` and `docs/PUBLIC_RELEASE_CHECKLIST.md` were
  corrected in place.
- The material corrections: provider documentation described Groq as the only
  cloud provider, and Mistral and Gemini appeared in no user-facing document at
  all; AUTO and CLOUD mode semantics were wrong; conversations were described as
  read-only; Memory/RAG was listed as roadmap and the Second Brain was
  undocumented; `edge-tts` was described as a *local* TTS provider when it sends
  the spoken text to Microsoft; a "cloud audio fallback" was documented that has
  no implementation; the count of eel-exposed functions was stale; and
  `MODEL_ROUTING.md` described `core/model_router.py` as if it decided the
  provider, which it never has.
- `PRIVACY.md` now states two things it previously got wrong: that a cloud
  provider tried and failed during AUTO failover **has already received the
  request**, and that approved tool results — clipboard text, file contents,
  extracted web pages — are sent to the provider so the model can continue.
  Screenshots remain the genuine exception: the tool returns a path and a size,
  never pixels.
- Documentation only; no production, frontend or test source changed.

### Provider routing evidence and AUTO order — 2026-09-09

- Replaced the cloud fallback order with a **measured** one. It had been
  `(google, groq, mistral)`, which is alphabetical and was nobody's decision;
  it is now `groq → mistral → google`, then Ollama as the terminal local hop.
- Committed the benchmark that decided it to `benchmarks/provider_routing/` —
  a 53-case synthetic corpus, per-case verdicts and latencies, the scoring
  weights stated before the numbers were read, and the caveats: one account,
  one day, one run per case, and Google under-measured by rate limits. The
  artifact deliberately makes no claim that any vendor is objectively better.
  A test fails if the constant and the exported results stop agreeing.
- Clarified throughout that `preferredCloud` (a user setting, always the first
  hop) and `CLOUD_PROVIDER_IDS` (the system order for everyone after it) answer
  different questions. `DEFAULT_CLOUD_PROVIDER` stays `groq`; this run
  reaffirmed it rather than changing it.
- Model defaults may now be adopted from an account's own catalogue for Google
  and Mistral, reported through a `model_source` field, and never override a
  model the user chose.

### Windows close lifecycle — 2026-09-09

- `window.close` now verifies the outcome against the window's **identity** —
  handle plus owning process id — rather than the handle alone, because Windows
  recycles handles and a new window can land on the same integer.
- A poll where the owner PID cannot be read falls back to trusting `IsWindow`
  for that poll instead of comparing against a zero and reading as a mismatch,
  which had reported a window closed after one flaky read.

### Memory extraction V2 — 2026-09-08

- Inferred facts are now **scored** rather than all being parked as inert
  candidates: a high-confidence inference becomes an active memory, a weaker one
  stays a candidate, and a weaker one still is discarded. Extraction remains
  deterministic and local — never a model call, because a classifier deciding
  what to remember would be a second, unauditable authority over the store.
- Separated durable facts from passing reminders, so a reminder no longer
  becomes something Nano believes about you permanently.

### Execution Ledger hardening — 2026-09-08

- The per-turn duplicate-execution ledger is now keyed on the **effective** call
  — the arguments after the registered schema has been applied — and that same
  normalised object is what the executor runs. Previously the same integer
  serialised three ways was three ledger identities for one effect, so a
  mid-turn provider failover could move the volume twice.
- Paths are normalised for case and separator, and enum arguments case-folded,
  so two spellings of one file or one direction are one entry.
- Identical calls **in flight** are covered too: a call is recorded before it is
  awaited, so a model emitting the same tool call twice in one response no
  longer executes it twice. Only a success is recorded durably, so a refusal
  stays retryable within the turn.

### Test reliability and real Chromium CI — 2026-09-07

- The Chromium UI tests now **actually run in CI**. Every module looked for
  `electron.exe`, a filename that cannot exist on Linux, so on CI they skipped
  and the skip read as a pass. The `chromium-ui` job now loads the production
  bundle into Electron's own Chromium under `xvfb` and runs all 57 of them.
- Fixed two harness races around CSS transitions that made graph rendering
  assertions flaky.

### Conversation continuity — 2026-09-06

- **Each turn stays bound to the conversation it was written in.** A message now
  belongs to the thread that was active when it was sent, not to whichever
  thread happens to be open when the answer arrives.
- Added bulk conversation deletion as one narrow, confirmed operation taking a
  list of thread ids — not a generic "delete where" reachable from the renderer.
  The confirmation names the count and the message total.

### Multi-provider support — 2026-09-04

- Added **Google (Gemini)** and **Mistral** alongside Groq, each with its own
  adapter, its own credential slot and its own model discovery. One key can
  never satisfy or overwrite another provider's slot.
- Surfaced all of it in the interface: per-provider keys, per-provider model
  selection, and a preferred-cloud setting.
- Routing became genuinely multi-provider: AUTO now walks the preferred cloud,
  then the remaining ready clouds, then Ollama, and reports **every hop it
  made** with what happened to each — so "you chose Gemini, it rate-limited,
  Groq finished the turn" is expressible rather than being flattened into
  "groq (fallback)".
- Hardened security grading and permission grounding alongside it.

### Conversation threads, memory and the Second Brain — 2026-08-31 → 2026-09-05

- **Conversation threads.** Real threads in SQLite, with creation, switching,
  renaming, archiving and deletion. Opening an older conversation rebuilds the
  model's context from that thread's messages and summary, which is what makes
  an old conversation writable instead of a read-only transcript.
- **Long-term memory** across threads, with retrieval, and a single context
  composer that decides what the model is told about the past — recent turns,
  the thread summary, relevant older messages, relevant memories — under a
  per-section token budget with deduplication, so CLOUD, AUTO and LOCAL receive
  logically identical context.
- **Second Brain**: nodes for the entities memories are about, and edges between
  nodes that co-occur in a memory. Nodes come only from active memories or
  explicit user action, never from raw message text. Implemented and tested; in
  practice still sparse, which is what a graph looks like before prolonged real
  use.
- One database, migrated in place under `PRAGMA user_version` and never
  replaced; existing messages were back-filled into real threads rather than
  discarded.
- Replaced trust-boundary tests that asserted on source layout with tests that
  exercise the behaviour.

### Public release foundation — 2026-08-30

**Security**

- Withdrew the `context_switcher` plugin. It exposed `context_activate_mode` to
  the model and its handler ran PowerShell twice (volume and a Focus Assist
  registry write), force-killed processes with `taskkill /F`, launched
  applications through `subprocess.Popen(..., shell=True)` — the shipped
  `hacker` mode opened Windows Terminal, an interpreter `pc_app_launch`
  explicitly refuses — and resolved its mode file from a model-supplied name
  with no path containment. It presented as a low-risk "activate work mode"
  confirmation.
- Removed `launch_process()` and `kill_process()` from `core/desktop_agent.py`.
  Both used `shell=True`; neither was referenced anywhere, which is precisely
  why they were worth deleting rather than leaving.
- Added Origin enforcement to the local control plane
  (`core/local_control_plane.py`). eel performs no Origin validation at all, so
  any web page could have opened Nano's WebSocket and called roughly seventy
  exposed functions — including `confirm_action`, `resolve_permission` and
  `set_emergency_stop`, the entire approval surface.
- Added a Content Security Policy to the Electron main window, verified against
  the real production bundle with a negative control proving it enforces.
- Removed `core/project_agent.py` after a repository-wide reference audit found
  it genuinely unreferenced. It ran `git` and `pytest` in a caller-supplied
  directory with no path containment; running pytest in an attacker-chosen
  directory would have executed that directory's `conftest.py`.

**Licensing**

- Nano is now licensed under **Apache License 2.0** (`LICENSE`), a decision
  made by the project owner. `THIRD_PARTY_NOTICES.md` and
  `docs/PUBLIC_RELEASE_CHECKLIST.md` updated accordingly; third-party
  obligations (notably the LGPL dependencies `edge-tts` and `pygame`) still
  apply independently of Nano's own licence.

**Documentation**

- Added `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SUPPORT.md`,
  this changelog, issue and pull-request templates.
- Added `PRIVACY.md` documenting the real data flows per provider mode, and
  `THIRD_PARTY_NOTICES.md` drafted from installed dependency metadata.
- Added `docs/RELEASING.md` and `docs/PUBLIC_RELEASE_CHECKLIST.md`.

**Continuous integration**

- Reworked CI into fast cross-platform checks plus a Windows job, with least
  privilege, pinned action majors, dependency caching, and no secrets.

### Settings V2 and information architecture — 2026-08-29

- Reorganised navigation so each destination answers one question: Chat,
  Ferramentas (a capability catalogue read from the live tool registry), PC
  (this computer's state, permissions and activity), Memória, Definições.
- Rebuilt Settings into seven categories behind a rail: Geral, IA, Voz, PC
  Control, Memória, Privacidade, Sobre.
- Made the top-right AI pill a working mode selector for AUTO / CLOUD / LOCAL,
  sharing one source of truth with Settings → IA and persisting the choice.
- Established a single canonical product version in `version.json`, read by the
  frontend, the Electron shell and the Python backend, replacing four
  independent version strings that disagreed.
- Fixed the fallback report naming the provider and model that had *failed*
  rather than the one that actually answered.

### PC Control V2 Ultra — 2026-08-29

- Expanded Windows control across applications, windows, audio and media,
  files and folders, web, display, input and clipboard, system, and power and
  session — every capability a narrow tool with typed arguments.
- Declared what Nano deliberately cannot do in `core/capabilities.py`, so the
  model is grounded to say so plainly instead of offering a confirmation for a
  capability that does not exist.
- Removed a live `shell.execute` tool from the executor. It ran
  `subprocess.run(["cmd", "/c", <model string>])` behind an approval dialog and
  was reachable by name even though it was never advertised.
- Rebuilt approval cards around what a person can judge — action, target, scope
  — with a preview where the size of the decision is not visible from the target.

### Ember interface and secure desktop architecture — 2026-08-23 → 2026-08-26

- Redesigned the desktop interface around the Ember visual identity.
- Shipped the Electron desktop shell with `contextIsolation`, `sandbox`,
  `nodeIntegration: false`, a narrow preload, restricted navigation and denied
  permission requests.
- Added the global voice hotkey and the voice overlay window.
- Added secure PC control with a robust local fallback.

### Voice and speech accuracy — 2026-08-22 → 2026-08-23

- Unified the voice runtime and the wake-turn handling.
- Moved Portuguese speech recognition to Whisper `small`, measurably more
  accurate than `tiny` at a higher CPU cost, and left wake-phrase listening off
  by default as a result.

### Provider routing and fallback hardening — 2026-08-19 → 2026-08-21

- Added AUTO / CLOUD / LOCAL provider modes with a single routing authority:
  CLOUD never silently downgrades, LOCAL never contacts the cloud (not even for
  a status probe), and an AUTO fallback is always visible rather than silent.
- Added provider and secret management with OS-encrypted key storage, and
  overhauled the settings interface.
- Hardened the core execution path and redesigned the command centre.

### Foundations — 2026-08-16 → 2026-08-20

- Initial project structure, the permission and policy architecture, the
  launcher, and the retirement of the legacy HELIOS branding.
