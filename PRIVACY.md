# Privacy

What Nano stores, where it stores it, and what leaves your computer.

This is project documentation describing the behaviour of the code in this
repository. It is **not a legal privacy policy** and not a substitute for
professional legal advice for a commercial or public deployment. It also
describes `main`, not a released version — there is no released version.

## The short version

Three things leave your computer, and nothing else does:

1. **Your message text**, sent to **Groq** — only in **AUTO** and **CLOUD**
   modes, only when Groq actually answers.
2. **The text Nano speaks aloud**, sent to **Microsoft** — in **every mode,
   including LOCAL**, whenever spoken replies are on.
3. **A web page you asked Nano to open or search**, fetched from that site.

Everything else — your conversation history, your remembered facts, your voice
recordings, the transcription of what you said, screenshots, the permission
audit trail, logs — stays on your machine.

> **The spoken-reply case is the one people get wrong.** "Local mode" means
> *the model* is local. Nano's text-to-speech uses Microsoft's Edge voice
> service, so the sentence it is about to read is sent to Microsoft to be turned
> into audio even when the model itself never touches the network. If you want
> nothing at all to leave the machine, turn off spoken replies in
> **Definições → Voz**.

---

## What leaves the computer, by mode

The mode is the pill at the top right, and Definições → IA.

### LOCAL — Ollama only

| Data | Leaves? | Where to |
| --- | --- | --- |
| Message text | **No** | Stays in `localhost:11434` (Ollama on your machine) |
| Groq | **Never contacted** | Not even for a status probe |
| Spoken replies (if on) | **Yes** | Microsoft — `speech.platform.bing.com` |
| Voice recording / transcription | **No** | Whisper runs locally |
| Web search / page fetch, if you ask for one | **Yes** | The site you asked for |

Groq is not contacted in LOCAL mode *at all* — this is enforced in
`core/provider_status.py`, which skips even the availability check, and is
covered by a test that asserts the credential is never read.

### CLOUD — Groq only

| Data | Leaves? | Where to |
| --- | --- | --- |
| Message text + recent conversation context | **Yes** | Groq (`api.groq.com`) |
| Ollama | **Never contacted** | — |
| Spoken replies (if on) | **Yes** | Microsoft |
| Voice recording / transcription | **No** | Whisper runs locally |

If Groq is unavailable in CLOUD mode, Nano says so. It does **not** silently
fall back to the local model — that is the whole point of choosing CLOUD.

### AUTO — Groq first, local fallback

Behaves as CLOUD while Groq is healthy, and as LOCAL after a Groq failure. When
it falls back, the interface says so: the pill shows a `fallback` tag and the
per-message details name the model that actually answered.

### What is sent to Groq, exactly

Your message, plus recent conversation context (trimmed to a token budget), plus
Nano's system prompt, plus the schemas of the tools relevant to that message.
Results of tools that ran are included so the model can continue from them.

**Not sent:** your API key is sent as an auth header, never as content. Your
remembered facts are included only if you have memory enabled and they are
relevant. Screenshots are never uploaded. Clipboard contents are never uploaded.
File contents are never uploaded unless you explicitly asked Nano to read a file
into the conversation.

Groq's own handling of what it receives is governed by Groq's terms, not by
this document.

---

## What is stored, where, and for how long

The data directory is `%LOCALAPPDATA%\NanoAssistant` on Windows
(`~/.local/share/NanoAssistant` elsewhere), overridable with `NANO_DATA_DIR`.
Definições → Sobre shows the real path.

| What | Where | Retention | Can you delete it? |
| --- | --- | --- | --- |
| Conversation history | SQLite in the data directory | Until you clear it | **Yes** — Definições → Privacidade → "Limpar conversa atual" |
| Remembered facts | Same database | Until you forget them | **Yes** — Memória page (one at a time) or Definições → Memória → "Esquecer tudo" |
| Settings | `user_settings.json` in the data directory | Until changed | Yes, by editing or resetting |
| API key | OS-encrypted store (**DPAPI** on Windows) | Until removed | **Yes** — Definições → IA → remove key |
| Screenshots | `screenshots/` in the data directory | **Auto-deleted: 1 hour, or the 10 most recent** | Yes, and they expire on their own |
| Permission audit trail | In memory only | **Lost when Nano closes** | Closing Nano clears it |
| Logs | `logs/nano.log` in the project folder | Rotates at 5 MB, 3 files kept | Yes, delete the files |
| Voice recordings | Temporary file, deleted immediately after transcription | Seconds | Automatic |
| Spoken audio | Temporary file, deleted after playback | Seconds | Automatic |
| Clipboard | **Never stored** | — | — |
| Typed-input text (PC Control) | **Never stored** — only a hash reaches the audit trail | — | — |

### Notes on specific items

**The API key** is stored encrypted by Windows DPAPI, tied to your Windows user
account. It is never sent to the interface, never written to a log, and never
appears in a tool result — the backend sends only a masked description like
`gsk_…abcd`. Definições → Privacidade shows whether encryption is actually
active rather than assuming it.

**Screenshots** are written to disk because the model needs a real file to look
at. They are deleted after an hour or once eleven exist, whichever comes first.
They are never uploaded anywhere. Taking one always asks first.

**The permission audit trail** — every PC action Nano took, with its target and
outcome — lives only in memory and disappears when Nano closes. It is what
PC → Atividade displays. It deliberately never records clipboard contents or
typed text: those are stored as a digest, so the trail can distinguish two
actions without holding what was written.

**Logs** record Nano's activity, which can include application names, window
titles and file paths. They are local, gitignored, and rotate. If you attach a
log to a bug report, read it first — see [SUPPORT.md](SUPPORT.md).

**Voice.** Wake-phrase detection and transcription both run locally with Whisper.
Audio is written to a unique temporary file and deleted immediately after
transcription. Recordings are never uploaded and never kept. Wake-phrase
listening is **off by default**, because it means holding the microphone open
continuously.

---

## What Nano never does

* Never sends telemetry, analytics or crash reports anywhere. There is no
  reporting endpoint in the codebase.
* Never uploads screenshots, clipboard contents or files you did not ask it to
  read.
* Never stores your API key in the project folder or in browser storage.
* Never runs arbitrary commands — see [SECURITY.md](SECURITY.md).
* Never acts on a destructive request without asking, showing you the action,
  the target and the scope first.

## Clearing your data

| To clear | Where |
| --- | --- |
| The current conversation | Definições → Privacidade |
| Everything Nano remembers about you | Definições → Memória → "Esquecer tudo" |
| One remembered fact | Memória page |
| Your API key | Definições → IA |
| Screenshots | Expire automatically; or delete `screenshots/` |
| Logs | Delete `logs/nano.log*` |
| Absolutely everything | Delete the data directory shown in Definições → Sobre |

There is currently **no single "delete all my data" button.** The individual
controls above cover everything, but a one-click wipe does not exist yet and is
tracked in [`docs/PUBLIC_RELEASE_CHECKLIST.md`](docs/PUBLIC_RELEASE_CHECKLIST.md).

## Third parties

| Service | When | What it receives |
| --- | --- | --- |
| **Groq** | AUTO and CLOUD modes | Message text and conversation context |
| **Microsoft** (`speech.platform.bing.com`) | Spoken replies, any mode | The text to be read aloud |
| **Ollama** | LOCAL mode and AUTO fallback | Message text — but it runs on your machine |
| **Websites** | Only when you ask Nano to open or search | A normal web request |

Ollama is listed for completeness: it is a local server, and the request never
leaves `127.0.0.1`.

## Verifying this yourself

None of this has to be taken on trust. The provider isolation is verified at the
HTTP transport layer — a test records every outbound request by host and asserts
that LOCAL mode contacts only `127.0.0.1` and CLOUD mode only `api.groq.com`.
You can watch the same thing with any network monitor.
