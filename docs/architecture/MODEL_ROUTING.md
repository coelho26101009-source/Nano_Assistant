# Nano Model Routing

Which provider answers a turn, which model it uses, and why.

## The one routing authority

Every chat turn's provider is decided in exactly one place:

```
core.providers.resolve_route(mode, groq, ollama, *, google, mistral, preferred, tier)
```

`Brain.chat` calls it; nothing else may decide a provider. It returns a single
decision object — provider, model, mode, tier, whether the choice is a fallback,
the reason in plain language, and the ordered alternatives that were ready at
decision time — so the interface can say *what actually happened* rather than
implying the primary answered.

> **`core/model_router.py` is not this.** It is a separate scoring abstraction,
> and on the live path it does one job: choosing *which local Ollama model* to
> use when the local model is configured as `auto`. It does not choose between
> Groq, Mistral, Google and Ollama. Earlier versions of this document described
> that module as if it were the routing decision; it never has been.

## The providers

| id | Adapter | Role |
|---|---|---|
| `groq` | Groq SDK (`core/providers.py`) | Cloud. The measured baseline and the default preference |
| `mistral` | OpenAI-compatible REST (`core/mistral_provider.py`) | Cloud |
| `google` | Gemini REST (`core/google_provider.py`) | Cloud |
| `ollama` | Local HTTP (`127.0.0.1:11434`) | Local, and the terminal fallback |

Adding a provider means adding an id to `CLOUD_PROVIDER_IDS` and a `describe_*`
function. Nothing in the Brain changes — which is what adding Mistral
demonstrated. `Brain._cloud_round` is the only method that knows more than one
cloud vendor exists; below that line, tool execution, the duplicate-execution
ledger, the failure taxonomy and the diagnostics are provider-agnostic.

## The three modes

### CLOUD — the preferred cloud provider, and only that one

```
preferred cloud provider
```

No cloud substitution, no Ollama fallback. "Use this provider" is an
instruction; quietly answering from a different vendor is the same class of
surprise as falling back to local without saying so. If the provider cannot
serve the turn, Nano says so and stops.

One deliberate exception to *not attempting*: a provider in `SETUP_REQUIRED`
(no credential, or no model chosen) is skipped, because the request cannot
succeed and the right output is the setup message. Every other unhealthy state
is still attempted, because the status snapshot behind it can be up to 45
seconds old and a stale probe must not become a refusal to work. That includes
`UNKNOWN`: a preferred provider whose status probe has not answered within the
route wait (below) is still tried with its configured model.

A CLOUD turn waits for the preferred provider's status and **no other**. The
other cloud providers can never serve it, so their probes -- however slow --
cannot delay it.

### AUTO — preferred cloud first, remaining clouds next, Ollama last

```
preferred cloud provider
  → the remaining cloud providers, in CLOUD_PROVIDER_IDS order
      → Ollama (local, terminal)
```

A hop is dropped from the chain, and the reason recorded, when the provider has
no credential, no model, no client in this process, or is inside a cooldown from
a recent rate limit. A provider whose status probe had not answered when the
turn was routed is `UNKNOWN` and never a first choice; the route records it in
`passed_over` when it came before the provider chosen. If the chain then runs
out on a fallback-eligible failure, a provider whose status has **since**
answered `READY` may still finish the turn -- see *Late alternatives* below.

### LOCAL — Ollama only

No cloud provider is contacted, **not even for a status probe**: the payloads
are synthesised locally by `core/provider_status.py`. That is a privacy
property, not an optimisation, and adding a third cloud provider did not weaken
it. See [`PRIVACY.md`](../../PRIVACY.md).

## `preferredCloud` is not the fallback order

These are two different settings answering two different questions, and
conflating them is the mistake this section exists to prevent.

| | What it answers | Where it lives |
|---|---|---|
| **`preferredCloud`** | *Who goes first?* | A **user setting**, Definições → IA |
| **`CLOUD_PROVIDER_IDS`** | *Who follows, in what order?* | A **system tuple**, `core/providers.py` |

The preferred provider is moved to the front of the chain; the rest keep the
declaration order of `CLOUD_PROVIDER_IDS`. So a user who prefers Google gets
Google first and then `groq, mistral` behind it — the tuple is not reordered by
the preference, and the preference is not overridden by the tuple.

Current values:

```
CLOUD_PROVIDER_IDS      = ("groq", "mistral", "google")
DEFAULT_CLOUD_PROVIDER  = "groq"        # used when the user expressed no preference
```

`DEFAULT_CLOUD_PROVIDER` is stated separately rather than derived from
`CLOUD_PROVIDER_IDS[0]`, so that reordering the fallback chain cannot silently
move the default. A test asserts the two nevertheless agree.

The rest order is deliberately **stable rather than measured live**. Ordering by
observed latency would make the same question route differently on two
consecutive turns for reasons the user cannot see.

## The evidence behind the order

The order is a measurement, not an alphabet — it used to read
`(google, groq, mistral)`, which is alphabetical and was nobody's decision. The
run that produced the current tuple is committed at
[`benchmarks/provider_routing/README.md`](../../benchmarks/provider_routing/README.md),
and `tests/test_provider_routing_policy.py` fails if the constant and the
exported results stop agreeing. Changing the order means re-running the
benchmark and re-exporting it.

In short:

* **Groq first** — two to three times faster on median turn (452 ms against
  1000 ms for Mistral and 1421 ms for Gemini), measured over 48 of the 53 cases,
  and the best tool accuracy among the broadly-covered providers: 93.8%, which
  is 15 of 16 tool cases. A primary answers most turns, so its latency is the
  one the user lives with. Its real weakness is Portuguese consistency (87.1%).
* **Mistral second, because of what a fallback is for** — the only cloud
  provider that completed the whole corpus with no rate-limit event at all, and
  a fallback is reached precisely when the first choice has just failed. Perfect
  European Portuguese. Its tool accuracy — 81.2%, or 13 of 16 tool cases — is
  why it is not first.
* **Google last, and not for answering badly** — on what it answered it scored
  best (88.2% pass, no observed security failure). It is last because on this
  project's credentials it mostly could not be asked: four rate-limit stops
  across its two models, only 17 of 53 cases measurable for
  `gemini-2.5-flash`, and a fresh 429 on the first request of three consecutive
  retry windows spaced 150 s apart. A hop that is usually unavailable spends a
  round trip and a cooldown to achieve nothing. That is a fact about a free
  tier on one day, not a claim about Gemini.
* **Ollama terminal, and not ranked by measurement** — local is last because it
  is the privacy fallback and the only provider that costs the user's own RAM.

The caveats travel with the numbers and are not optional: **one account, one
day, one run per case.** Google is under-measured, so its quality is unranked.
On task quality Groq and Mistral are statistically indistinguishable at this
corpus's resolution — the ranking between them flips depending on a weight
somebody chose — and the order rests on latency and availability, which are the
differences that are large and repeatable. None of this is a claim that any
vendor is objectively better than another.

Nothing in the runtime reads a benchmark figure. Rate limiting is handled by
reacting to a real 429 and its `Retry-After`, never by a quota number copied
from a dashboard.

## Model selection

Each provider payload carries a `fast` and a `complex` tier, and the task
classifier (`core/model_selection.py`) picks which tier a turn needs.
`cloud_model_for(payload, tier)` resolves the concrete id.

**Concrete model ids are discovered from the account, never pinned blindly.**
Pinning a literal id is what once left the project calling a decommissioned
model and 404-ing on every message, so `cloud_model_choices` validates a choice
against the account that will serve it.

**Default adoption.** When an account is credentialed but no model has been
chosen, `core/model_defaults.py` may adopt one from the account's own catalogue
— currently the behaviour that matters for Mistral and Google. Every payload
reports a `model_source` (`configured`, `default`, or `none`) so the interface
can show whether a model was picked by the user, adopted from the catalogue, or
is still missing. **A user's own selection is never overridden**: a default
exists to make a credentialed provider usable, not to be the best possible
pick. The benchmark makes the point concretely — `gemini-2.5-flash-lite`, which
the resolver would adopt for an unconfigured Google account, scored well below
the configured `gemini-2.5-flash` on tool accuracy.

## Status snapshots and cooldowns

Describing a provider costs a synchronous HTTPS round trip with a 10-second
timeout. `core/provider_status.py` holds **one snapshot**, keyed by mode, model
and preference, shared by the Brain and the Settings page, refreshed off-thread,
with a 45-second TTL. The cloud probes run concurrently, so a cold snapshot with
three providers costs one timeout rather than the sum of three.

Rate limits are read from the response: `parse_rate_limit` interprets the
provider's headers and `Retry-After`, and `core/provider_failures.py` holds a
per-provider cooldown. In AUTO a cooling-down provider is dropped from the
chain, because asking a provider we know is rate-limited costs latency, fails,
and spends a budget that has not come back yet. In CLOUD it is still attempted —
the user asked for that provider specifically.

### What a chat turn waits for

Status is evidence for a decision, not a prerequisite for sending. A fresh
snapshot is used as it is. Otherwise the turn shares the one refresh in flight
(or starts it) and waits **only until its decision is settled** — the rule
`providers.route_pending` states, asked about the cloud half:

| Mode | The turn stops waiting when |
|---|---|
| LOCAL | at once: no cloud provider is asked anything, so there is nothing to wait for |
| CLOUD | the preferred provider has answered |
| AUTO | every candidate up to the first `READY` one has answered |

and never later than `provider_status.ROUTE_WAIT_SECONDS` (3 s). Each probe
reports into the refresh the moment it lands, which is what makes stopping
early possible. The refresh itself always completes in the background and is
cached for the next turn and for the Settings page.

Before this, a cold or expired snapshot made every turn wait for **every**
configured provider's probe. Measured with one configured provider that
accepted connections and never answered: 10.5 s per turn in AUTO and 10.6 s in
CLOUD, although the preferred provider had answered at once and was the one
that answered. Both are now ~0 s; a hung *preferred* provider in AUTO costs the
route wait and is then passed over.

### Late alternatives

Stopping early means a provider can still be `UNKNOWN` when a turn starts, and
`UNKNOWN` is never an alternative. So when the chain runs out on a
fallback-eligible failure in AUTO, the Brain looks once more
(`Brain._late_alternatives`): a provider whose probe has **since** answered
`READY` joins the chain, under every usual rule (credential, model, cooldown).
This is what keeps *Groq rate-limited → Mistral finishes* working when Groq's
probe happened to land first. The lookup:

* never **starts** a probe — it shares the refresh the turn's route began, or
  reads what that refresh cached;
* waits only for what is left of the same per-turn budget, so a hung provider
  costs a turn `ROUTE_WAIT_SECONDS` at most, however the turn divides it;
* happens at most once per turn;
* does nothing if the user has switched away from AUTO meanwhile: a turn that
  began in AUTO does not reach a cloud provider it had not already chosen after
  the user asked for LOCAL.

## Failover policy

`core/provider_failures.py` classifies every provider exception once; the class
decides whether another provider may finish the turn.

| Failure | AUTO | CLOUD | Cooldown |
|---|---|---|---|
| `RATE_LIMIT` (429) | next provider | honest error | the longer of `Retry-After` and the token reset, 2-120 s |
| `TIMEOUT` (408, read/connect timeout) | next provider | honest error | 15 s |
| `CONNECTION_ERROR` (DNS, refused, offline) | next provider | honest error | 15 s |
| `SERVER_ERROR` (5xx) | next provider | honest error | 20 s |
| `MODEL_UNAVAILABLE` (404) | next provider | honest error | 60 s |
| `NOT_CONFIGURED` (key removed mid-turn) | next provider | honest error | none |
| `UNKNOWN_PROVIDER_ERROR` | next provider | honest error | 10 s |
| `AUTH_ERROR` (401/403) | **stops**: the user must fix the key | honest error | none |
| `BAD_REQUEST` (other 4xx) | **stops**: our request was wrong | honest error | none |
| `CANCELLED` | **stops**, nothing else is started | — | none |

Three rules sit above the table. **CLOUD never substitutes a provider.** **A
half-streamed answer is never replaced** by a second one. **No provider is
retried within a turn**: each is asked at most once, and the chain is bounded by
the number of providers. Failure sentences name the provider that failed and
never carry its raw error text.

The local model follows the same spirit. If Ollama cannot be **reached** at all
(connection refused or not answering the connect), the turn says so after one
attempt; the retry without tools exists for a server that rejected what it was
sent, and asking a stopped server twice only doubled the wait. A non-streamed
local answer gets `LOCAL_READ_TIMEOUT_SECONDS` (180 s): 6 of the 53 local turns
in the committed benchmark, sent in exactly this shape, took 67-124 s, and the
old 60 s budget cut each of them off and re-asked without tools or history.

## Client lifecycle and credentials

* **One TLS context per process** (`core/http_tls.py`), shared by every HTTP
  client Nano builds. httpx otherwise parses certifi's CA bundle for every
  client: ~200 ms each on Windows, twice with a proxy. That parse used to run
  on eel's hub whenever a key was saved or removed (the Groq client was rebuilt
  there: 200-530 ms during which every other bridge call waited) and on the
  chat event loop once per streamed Gemini/Mistral round and per local turn
  (204-269 ms each). Verification is unchanged: it is httpx's own default
  context, built once. `core.main` builds it before eel serves.
* **A client is built for exactly one key, and only when that key changes.**
  `reload_cloud_credentials` re-reads all three keys; an unchanged key keeps
  its client and its open connection, a changed one gets a new client, and a
  removed one leaves `None` before the call returns.
* **A key removed mid-turn is final.** Every round reads its client afresh, so
  the round after a removal fails as `NOT_CONFIGURED` — "the key is no longer
  configured", never "the key was refused" — and AUTO continues on the next
  provider under the table above.
* Nothing is built in the background, so there is no late result that could
  reinstate a client after a removal. Keys are compared in constant time and
  never logged.

## Offline and degraded networks

| Situation | What happens |
|---|---|
| No internet, Ollama running, AUTO | probes fail fast (UNAVAILABLE); the turn answers locally, marked as a fallback |
| No internet, Ollama running, CLOUD | the preferred provider is tried and the honest error shown; Ollama is not contacted |
| No internet, Ollama down | "no provider available": one local attempt, then the plain sentence |
| Network that accepts but never answers | status costs a turn at most `ROUTE_WAIT_SECONDS`; the chat request itself is bounded by the provider's own timeout (Groq: connect 5 s / read 60 s; Gemini and Mistral: connect 10 s / read 90 s) |
| LOCAL, whatever the network | nothing leaves the machine; no cloud status probe either |

No bridge call waits on any of these: status reads on eel's hub never touch the
network (see `get_stale_ok`), and every wait above happens on a worker thread.

## Attribution: what the user is told

A single "provider: groq (fallback)" is a true statement that answers the wrong
question. If the user selected Gemini, the fact they need is *"Gemini was asked,
it returned a service error, Groq finished the turn."*

So `Brain.last_metadata` carries `provider_attempts` — every hop this turn made,
in order, each with its model and its outcome — alongside `provider`/`model`
(who finished), `fallback_used`, `cloud_skipped` (who was dropped and why, with
the remaining cooldown in seconds), `tier`, `task` and the memory accounting.
`core/response_meta.py` decides what survives to the interface.

`tier` is set on every branch. It was once dropped on both AUTO branches, so the
diagnostics panel read `None` for it exactly when Nano had fallen back — the
case the panel most needs to explain.

The decision's own evidence travels beside it: `route_status` (was the status
`cached` — with its age — `measured` while the turn waited, or `partial`, with
the providers that had not answered) and `route_passed_over` (the AUTO
candidates before the chosen one, each with the state that ruled it out). Both
stay in the diagnostics scratchpad; the message allow-list does not persist
them. Each turn also logs one line in this shape, with names, states and
timings only — never a prompt, a key or a provider's error text:

```
Route: mode=AUTO provider=mistral model=… tier=FAST fallback=False status=[partial waited=3001ms unmeasured=groq] passed_over=[groq:UNKNOWN]
```

## The Execution Ledger: failover must not repeat an effect

A turn may cross providers. The one thing failover must never do is repeat a
real effect on the machine — open a second Calculator, close a second window,
move the volume twice.

`Brain._turn_tool_results` is a per-turn ledger keyed on `(tool name, canonical
arguments)`. The first execution of a call is remembered; an identical repeat
from a later provider is served the recorded result instead of touching Windows
again. The model still sees a truthful answer; the machine is acted on once.
The ledger is per turn, so a genuine second request in a later turn is
unaffected.

Two details carry the weight:

* **Identity is the *effective* call, not the JSON.** The registered schema is
  applied first, and its output is both what is fingerprinted and what the
  executor runs, so `{"delta": 10}`, `{"delta": 10.0}` and `{"delta": "10"}`
  cannot become three ledger entries for one effect. Paths are normalised for
  case and separators; enum arguments are case-folded.
* **In-flight calls are covered too.** A model can emit the identical tool call
  twice in one response, and both copies would pass a finished-only ledger
  before either wrote to it. A call is therefore entered as *in flight* before
  it is awaited, and an identical arrival waits for it rather than starting a
  second. The in-flight entry is removed whatever the outcome, so a refusal
  stays retryable; only a success is written to the durable ledger.

This protects against a provider **re-issuing a call it can see in the
history**. It is a per-turn deduplication of identical calls, not a general
transactional guarantee: a model that asks for a genuinely different action gets
a genuinely different action, which is the correct behaviour.

## Extending it

To add a cloud provider:

1. add its id to `ProviderId` and to `CLOUD_PROVIDER_IDS` (position is an
   evidence decision — see the benchmark);
2. add a `describe_<provider>` returning the standard payload shape;
3. register it in `_CLOUD_DESCRIBER_NAMES` / `_CLOUD_TESTER_NAMES`;
4. add an adapter that fills the shared collector in `Brain._cloud_round`,
   reading its client once per round and raising `ProviderNotConfigured` when
   it is gone;
5. build every httpx client it uses with `verify=http_tls.shared_context()`,
   and give its transport a constant-time `holds_key()` so an unchanged key
   keeps its client;
6. add its secret slot to `core/secret_store.py`.

The router, the cooldown registry, the settings surface and the diagnostics all
key on the same id and need no further change.
