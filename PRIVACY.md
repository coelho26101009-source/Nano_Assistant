# Privacy

What Nano stores, where it stores it, and what leaves your computer.

This is project documentation describing the behaviour of the code in this
repository. It is **not a legal privacy policy** and not a substitute for
professional legal advice for a commercial or public deployment. It also
describes `main`, not a released version — there is no released version.

## The short version

Four kinds of thing leave your computer, and nothing else does:

1. **Your message and the context Nano builds around it**, sent to whichever
   **cloud provider** answers the turn — **Groq**, **Mistral** or
   **Google (Gemini)**. Only in **AUTO** and **CLOUD** modes.
2. **A status probe** to each cloud provider you have configured a key for, in
   AUTO and CLOUD. It carries the key and asks which models the account has. It
   carries no part of your conversation.
3. **The text Nano speaks aloud**, sent to **Microsoft** — in **every mode,
   including LOCAL**, whenever spoken replies are on.
4. **A web page you asked Nano to open, search or read**, fetched from that
   site.

Everything else — your conversation history, your remembered facts, the Second
Brain, your voice recordings, the transcription of what you said, screenshots,
the permission audit trail, logs — stays on your machine.

> **The spoken-reply case is the one people get wrong.** "Local mode" means
> *the model* is local. Nano's text-to-speech uses Microsoft's Edge voice
> service, so the sentence it is about to read is sent to Microsoft to be turned
> into audio even when the model itself never touches the network. If you want
> nothing at all to leave the machine, turn off spoken replies in
> **Definições → Voz**.

> **A failed attempt still sent your data.** In AUTO, Nano may ask one provider,
> get a rate-limit or a service error, and finish the turn on the next one. The
> first provider *received the request* before it failed. The interface names
> the provider that **answered**; it is not a list of everyone who saw the
> message. Every hop that was attempted is recorded in the per-message technical
> details.

---

## The three modes, precisely

The mode is the pill at the top right, and Definições → IA. Alongside it there
is a **preferred cloud provider** (Definições → IA), which decides *which* cloud
provider goes first. The two settings are independent.

### LOCAL — Ollama only

| Data | Leaves? | Where to |
| --- | --- | --- |
| Message text and context | **No** | Stays in `127.0.0.1:11434` (Ollama on your machine) |
| Groq, Mistral, Google | **Never contacted** | Not even for a status probe |
| Spoken replies (if on) | **Yes** | Microsoft — `speech.platform.bing.com` |
| Voice recording / transcription | **No** | faster-whisper runs locally |
| Web search / page fetch, if you ask for one | **Yes** | The site you asked for |

No cloud provider is contacted in LOCAL mode *at all*. This is enforced in
`core/provider_status.py`, which synthesises each cloud provider's status
locally instead of probing it, and is covered by a test that asserts the
credentials are never read.

LOCAL means **the language model is local**. It does not mean absolute network
silence: spoken replies still reach Microsoft, and a web tool you explicitly
asked for still reaches the site.

### CLOUD — your preferred cloud provider only

| Data | Leaves? | Where to |
| --- | --- | --- |
| Message text + assembled context | **Yes** | Your preferred cloud provider, and only that one |
| Status probe (per configured provider) | **Yes** | Each cloud vendor you hold a key for |
| Ollama | **Never contacted** | — |
| Spoken replies (if on) | **Yes** | Microsoft |
| Voice recording / transcription | **No** | faster-whisper runs locally |

CLOUD honours your choice exactly. It does **not** quietly substitute a
different vendor, and it does **not** fall back to the local model — "use this
provider" is an instruction, and silently answering from somewhere else is the
surprise the mode exists to prevent. If the provider is unavailable, Nano says
so and stops.

The one thing CLOUD does contact more widely is the **status probe**: the
provider panel describes every cloud provider you have configured, so each of
them receives a "list your models" request. That request carries the API key for
that account and no conversation content.

### AUTO — preferred cloud first, then the rest, then local

| Data | Leaves? | Where to |
| --- | --- | --- |
| Message text + assembled context | **Yes** | The first provider that accepts it — **and any provider that was tried and failed before it** |
| Status probe (per configured provider) | **Yes** | Each cloud vendor you hold a key for |
| Ollama, on cloud failure | Local | `127.0.0.1:11434` |
| Spoken replies (if on) | **Yes** | Microsoft |

The attempt order is:

```
preferred cloud provider          (your choice; groq if you never chose)
  → the remaining cloud providers, in the order groq, mistral, google
      → Ollama, locally, as the terminal fallback
```

A provider is skipped without being contacted when it has no key, no model, or
is inside a cooldown from a recent rate limit. Everything else is attempted.

The declared order after your preference is an evidence-based decision, not a
ranking of vendors — see
[`benchmarks/provider_routing/README.md`](benchmarks/provider_routing/README.md)
and [`docs/architecture/MODEL_ROUTING.md`](docs/architecture/MODEL_ROUTING.md).

---

## What a cloud request actually contains

One turn's request is assembled by `core/context_composer.py`, which is the
single place that decides what the model is told. It contains:

* **your current message**;
* **the recent conversation**, verbatim, from this thread only, trimmed to a
  token budget;
* **a summary of the older part of this thread**, when one exists;
* **older messages from this same thread** that retrieval found relevant;
* **long-term memories** that retrieval found relevant, when memory is on;
* **Second Brain entries** for the entities those memories are about;
* **Nano's system and policy prompt**;
* **the schemas of the tools selected as relevant** to that message;
* **the results of tools that already ran this turn**, so the model can continue
  from them.

Whole databases are never uploaded. Retrieval selects a bounded number of
entries per section against a per-section token budget; what it selected is
reported in the per-message technical details.

### Tool results are part of the request

This is the part worth reading twice. Once a tool has run, **its result is sent
to the provider** so the model can carry on from it. That means the provider
sees whatever the tool returned:

| If you ask Nano to… | The provider sees |
| --- | --- |
| Read the clipboard | **the clipboard text** (up to 4000 characters) |
| Read a file into the conversation | **that file's contents**, as returned |
| Search or open a web page | **the extracted page text** |
| Look at system state, windows, volume | those values |
| Take a screenshot | **the file path and its size — never the image** |

Nano never reads any of these on its own initiative: each one is a tool the
model must call, and the sensitive ones require your approval first. But
"approved once" and "not transmitted" are different things, and the earlier
version of this document conflated them.

**Screenshots are the genuine exception.** `core/pc_control/screen.py` returns a
path, a size and dimensions — no base64, no pixels. The image is written to disk
for you, and no production path puts it into model context.

### What is not sent

* **Your API key is never content.** It travels as an authentication header to
  the provider that owns it, and nowhere else.
* **Typed text and clipboard contents never reach the audit trail** — only a
  digest, so two actions can be told apart without storing what was written.
* **No telemetry, analytics or crash reporting.** There is no reporting endpoint
  in the codebase.

What a provider then does with what it receives is governed by that provider's
own terms, not by this document.

---

## API keys

Credentials are **authentication material, not prompt content**. They are never
placed in a message, a tool result, a log line or the audit trail.

Storage (`core/secret_store.py`):

* On **Windows**, encrypted with **DPAPI** (`CryptProtectData`), so the
  ciphertext can only be decrypted by the same Windows user on the same machine.
  The file is `secrets.dat` in the data directory.
* **Elsewhere**, a `0600` file — weaker, same interface.
* **Environment variables are read as a fallback**, so an existing `.env` keeps
  working: `NANO_API_KEY` / `GROQ_API_KEY` for Groq, `NANO_GEMINI_API_KEY` /
  `GEMINI_API_KEY` / `GOOGLE_API_KEY` for Google, `NANO_MISTRAL_API_KEY` /
  `MISTRAL_API_KEY` for Mistral. Each provider has its own entry; one key never
  satisfies another's slot. Writing always goes to the encrypted store, never
  back to `.env`.
* **The interface never receives a key.** The backend sends only a masked hint
  like `gsk_…abcd` and a boolean. Definições → Privacidade reports whether
  encryption is actually active rather than assuming it.

---

## What is stored, where, and for how long

The data directory is `%LOCALAPPDATA%\NanoAssistant` on Windows
(`~/.local/share/NanoAssistant` elsewhere), overridable with `NANO_DATA_DIR`.
Definições → Sobre shows the real path.

Conversations, memories and the Second Brain share **one SQLite database** in
that directory. Its filename is `helios.db` — a legacy name kept deliberately so
existing installs are not stranded; see [`docs/README.md`](docs/README.md).

| What | Where | Retention | Can you delete it? |
| --- | --- | --- | --- |
| Conversation threads and messages | SQLite (`conversations`, `messages`) | Until you delete them | **Yes** — per thread, several at once, or all, from the conversation rail |
| Thread summaries and per-thread facts | Same database | Deleted with their thread | Yes, with the thread |
| Long-term memories | Same database (`memories`) | Until you forget them | **Yes** — Memória page, or Definições → Memória → "Esquecer tudo" |
| Second Brain nodes and edges | Same database (`knowledge_nodes`, `knowledge_edges`, `knowledge_links`) | Until deleted | **Yes** — per node, on the Memória → Second Brain page |
| Retrieval index | Same database (`retrieval_entries`) | Follows its source row | Yes, by deleting the source |
| Settings | `user_settings.json` in the data directory | Until changed | Yes, by editing or resetting |
| API keys | `secrets.dat`, OS-encrypted (**DPAPI** on Windows) | Until removed | **Yes** — Definições → IA, per provider |
| Screenshots | `screenshots/` in the data directory | **Auto-deleted: older than 1 hour, or beyond the 10 most recent** | Yes, and they expire on their own |
| Permission audit trail | In memory only (a list in `PermissionManager`) | **Lost when Nano closes** | Closing Nano clears it |
| Logs | `logs/nano.log` in the data directory | Rotates at 5 MB, 3 backups kept | Yes, delete the files |
| Voice recordings | Temporary file, deleted immediately after transcription | Seconds | Automatic |
| Spoken audio | Temporary file, deleted after playback | Seconds | Automatic |
| Clipboard | **Never stored** | — | — |
| Typed-input text (PC Control) | **Never stored** — only a digest reaches the audit trail | — | — |

### Notes on specific items

**Long-term memory** is written by `core/memory_extraction.py`, which is
deliberately **deterministic and local** — it is not a model call. A sentence
you explicitly asked Nano to remember is stored active. A sentence Nano merely
*inferred* is scored, and only a high-confidence inference becomes an active
memory; a weaker one becomes an inert candidate you can promote, and a weaker
one still is discarded. Nothing here is sent anywhere to be classified.

**The Second Brain** creates a node only from an active long-term memory or from
an explicit action of yours — never from raw message text — and writes an edge
only when two nodes appear in the same memory. It is a store of entities derived
from what you chose to keep, not a second copy of your conversation.

**Screenshots** are written to disk because a person may want to look at them.
They are deleted once they are older than an hour or once they are past the ten
most recent, whichever applies first. The image is never uploaded and never
enters model context. Taking one always asks first.

**The permission audit trail** — every PC action Nano took, with its target and
outcome — lives only in memory and disappears when Nano closes. It is what
PC → Atividade displays. It deliberately never records clipboard contents or
typed text: those are stored as a digest, so the trail can distinguish two
actions without holding what was written.

**Logs** record Nano's activity, which can include application names, window
titles and file paths. They are local, gitignored, and rotate. If you attach a
log to a bug report, read it first — see [SUPPORT.md](SUPPORT.md).

**Diagnostics** in Settings → About are an explicit local report of version,
platform, architecture, backend/frontend status and fixed error codes. The
report excludes paths, credentials and conversation contents. Copying it does
not upload it. Startup failures also write `logs/startup-diagnostics.json` in
the data directory, where writable. Uninstalling preserves this directory and
its conversations, memories, settings, encrypted credentials and policies.

**Voice.** Wake-phrase detection and transcription both run locally with
faster-whisper. There is no cloud speech-to-text path in the code: the runtime
constructs a local provider unconditionally. Audio is written to a unique
temporary file and deleted immediately after transcription. Recordings are never
uploaded and never kept. Wake-phrase listening is **off by default**, because it
means holding the microphone open continuously.

---

## Web requests you asked for

If you ask Nano to search, open or read a web page, that website — and the
network path to it — receives an ordinary HTTP request from your machine. This
is separate from provider traffic and happens in every mode, including LOCAL.

The page's *extracted text* then becomes a tool result, which means it is sent
to whichever provider is answering the turn. Browser automation requires the
optional `playwright` dependency and does nothing if it is not installed.

Requests are restricted to public HTTP(S) destinations
(`core.browser_agent.validate_public_http_url`): loopback, private and
link-local addresses, `file://` and other schemes are refused.

---

## What Nano never does

* Never sends telemetry, analytics or crash reports anywhere. There is no
  reporting endpoint in the codebase.
* Never uploads a screenshot image, and never reads a file, the clipboard or a
  web page except as a tool you asked for and, where it matters, approved.
* Never stores an API key in the project folder or in browser storage, and never
  sends one to the interface.
* Never runs arbitrary commands — see [SECURITY.md](SECURITY.md).
* Never acts on a destructive request without asking, showing you the action,
  the target and the scope first.

## Clearing your data

| To clear | Where |
| --- | --- |
| One conversation | Conversation rail → the thread's menu → "Apagar conversa" |
| Several conversations at once | Conversation rail → selection mode → "Apagar N conversas" |
| The messages of the conversation you are in | Definições → Privacidade → "Limpar conversa atual" |
| Everything Nano remembers about you | Definições → Memória → "Esquecer tudo" |
| One remembered fact | Memória page |
| One Second Brain node | Memória → Second Brain → the node → "Apagar nó" |
| An API key | Definições → IA, per provider |
| Screenshots | Expire automatically; or delete `screenshots/` |
| Logs | Delete `logs/nano.log*` |
| Absolutely everything | Delete the data directory shown in Definições → Sobre |

Deleting a conversation removes its messages, its summary, its per-thread facts
and its retrieval index entries. It does **not** remove long-term memories that
were extracted from it — those are cross-thread by design and are cleared from
the Memória page.

There is currently **no single "delete all my data" button.** The individual
controls above cover everything, but a one-click wipe does not exist yet and is
tracked in [`docs/PUBLIC_RELEASE_CHECKLIST.md`](docs/PUBLIC_RELEASE_CHECKLIST.md).

## Third parties

| Service | When | What it receives |
| --- | --- | --- |
| **Groq** (`api.groq.com`) | AUTO and CLOUD, when it is asked | Message text and assembled context; separately, a model-list probe |
| **Mistral** (`api.mistral.ai`) | AUTO and CLOUD, when it is asked | The same |
| **Google / Gemini** (`generativelanguage.googleapis.com`) | AUTO and CLOUD, when it is asked | The same |
| **Microsoft** (`speech.platform.bing.com`) | Spoken replies, **any mode** | The text to be read aloud |
| **Ollama** | LOCAL, and the AUTO fallback | Message text — but it runs on your machine |
| **Websites** | Only when you ask Nano to open, search or read one | A normal web request |

A provider you have not configured a key for is never contacted, in any mode:
its status is reported without a network call.

Ollama is listed for completeness: it is a local server, and the request never
leaves `127.0.0.1`.

## Verifying this yourself

None of this has to be taken on trust, though it is worth being exact about what
the tests do and do not prove.

`tests/test_multi_provider.py` runs the real Brain against recording provider
clients and a fake local server, and asserts on what each of them *received*:
that LOCAL mode sent nothing to Groq or Google, that CLOUD mode never fell over
to another vendor or to Ollama, and that an unconfigured provider was never
asked. A separate test calls `provider_status.describe_all` in LOCAL mode and
asserts every cloud provider comes back synthesised and disabled, so the
guarantee covers status probes and not only chat.

These are assertions at the provider-client boundary, not a packet capture. If
you want proof at the network layer rather than at that boundary, watch it with
any network monitor — that is the check nothing in this repository can fake.

The per-message technical details panel shows, for the turn you are looking at,
which providers were attempted, in what order, what happened to each, and how
much memory context was included.
