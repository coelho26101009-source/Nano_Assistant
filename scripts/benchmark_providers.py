"""Measure Nano's candidate models against each other. Never touches Windows.

WHY IT EXISTS
-------------
Nano's Groq models were chosen from a real measurement on this account, not
from documentation, and the note in config/settings.yaml records what that
measurement found. Adding a second cloud provider does not get to skip that
step: Google becomes the default only if it earns it here.

THE SAFETY PROPERTY, AND HOW IT IS ENFORCED
-------------------------------------------
A PC Control case asks a model to do something to the machine. The benchmark
scores the TOOL CALL IT PRODUCED and stops there. This is not a promise in a
docstring -- the code never constructs a ToolExecutor and never calls
``Brain._run_tool``; it builds the same prompt and the same tool subset the
Brain would, sends it straight to the provider adapter, and reads back the
intent. There is no code path from here into POLICY -> PERMISSION -> EXECUTION,
which is why no case can open an application, take a screenshot or touch a
file. ``--self-check`` proves it.

FREE QUOTAS ARE FINITE, SO THE RUN IS STAGED
--------------------------------------------
    PHASE A   the screening subset (10 cases) against every candidate
    PHASE B   the full corpus against the finalists you name
    PHASE C   the finalists against the Groq baseline

Every model has a hard request ceiling enforced by a counter, not by good
intentions: when the budget is spent the model is stopped mid-phase and the
report says so. A candidate with a 20-request daily quota must not have it
spent by a benchmark.

NO SECRET EVER REACHES THE OUTPUT
---------------------------------
Keys come from the encrypted store, are held in the transport, and are never
printed, logged or written to the report. The writer refuses to save a report
that contains one.

USAGE
    python scripts/benchmark_providers.py --self-check
    python scripts/benchmark_providers.py --list-models
    python scripts/benchmark_providers.py --phase a --models google:<id>,google:<id>
    python scripts/benchmark_providers.py --cases pt-01,mem-01 --models google:<id>
    python scripts/benchmark_providers.py --phase b --models google:<id>
    python scripts/benchmark_providers.py --phase c --models google:<id>,groq:<id>
    python scripts/benchmark_providers.py --phase a --models mistral:<id>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# THE SAME CREDENTIALS THE APPLICATION USES, FOUND THE SAME WAY.
#
# core.main calls load_dotenv(ROOT / ".env") before anything reads a secret, so
# a key that lives only in .env works in the product. This script imports
# core.brain rather than core.main, so without this line it would report
# "no_api_key" for a provider the running application can reach perfectly well
# -- and a benchmark that cannot see what production sees measures a different
# installation. The encrypted store still wins over the environment; see
# core.secret_store.get_secret.
try:
    from dotenv import load_dotenv                                            # noqa: E402

    load_dotenv(ROOT / ".env")
except ImportError:                                # pragma: no cover - optional
    pass

from core import (brain, capabilities, google_provider, mistral_provider,     # noqa: E402
                  model_selection, providers, secret_store)
from core.brain import base_system_sections                                   # noqa: E402
from core.plugin_loader import get_all_tools, load_all_plugins                # noqa: E402
from core.trust import TRUST_BOUNDARY_SYSTEM_RULES                            # noqa: E402
from scripts.benchmark_cases import (                                         # noqa: E402
    CASES, VERDICT_FAIL, VERDICT_PASS, VERDICT_REVIEW, Case, cases_by_id,
    cases_for, expected_tools, grade, phase_a_cases,
)

OUTPUT_DIR = ROOT / "runtime" / "benchmarks"

#: Per-model request ceilings, from the user's OBSERVED free-tier dashboard.
#:
#: These are budgets for THIS SCRIPT, not product constants, and nothing in the
#: Nano runtime reads them -- routing must never depend on a number somebody
#: copied off a dashboard on one particular day. The lowest ceiling exists
#: because one candidate reported roughly 20 requests per day, and a benchmark
#: that spends them all leaves the user unable to use the model it recommended.
DEFAULT_BUDGET = 8
LOW_QUOTA_BUDGET = 5

#: Shortest generation window that yields a meaningful characters-per-second
#: figure. Below it the divisor is dominated by measurement noise; see _finish.
MIN_THROUGHPUT_WINDOW_SECONDS = 0.05


@dataclass
class Result:
    """One case against one model. Contains no credential and no payload."""

    case_id: str
    category: str
    provider: str
    model: str
    ok: bool
    #: PASS / FAIL / REVIEW, or "UNMEASURED" when the provider never answered.
    #:
    #: ``ok`` alone could not carry this. A case marked ``review`` whose
    #: automated checks all passed used to be reported as ok=True, and that is
    #: exactly how the Mistral run printed "security 100%" for a model that had
    #: agreed in writing to stop asking for confirmation. An unread reply is
    #: not a pass, and a request that failed is not a pass either.
    verdict: str = "UNMEASURED"
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    needs_review: bool = False
    answer: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    first_token_ms: int | None = None
    total_ms: int | None = None
    chars_per_second: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    failure_type: str | None = None


# ---------------------------------------------------------------------------
# Prompt assembly: the same context every provider gets
# ---------------------------------------------------------------------------

def system_prompt_for(case: Case, with_tools: bool) -> str:
    """Nano's real system prompt, assembled the way Brain assembles it.

    The head of the prompt is not reassembled here. ``base_system_sections`` is
    the SAME function ``Brain._build_system_prompt`` calls, so the persona, the
    tool rules, the trust boundary and the capability-grounding block cannot
    differ between what the benchmark measures and what production sends.

    That distinction was not theoretical. The previous version of this function
    was a hand-maintained copy and it had drifted: it never appended
    ``capabilities.grounding_block``, so every SECURITY case scored a model that
    had never been told Nano has no shell. Comparison BETWEEN models stayed
    fair, because all of them were short the same block, but the absolute
    figures did not describe production Nano.

    Nothing executable is reachable through it: it reads two module constants
    and one pure regex matcher, and returns strings.

    The composed-context block stands in for the ContextComposer: the benchmark
    injects the case's facts through the SAME channel real memories travel
    through, so a memory case measures whether the model USES its context
    rather than whether it happens to know something. It is the one section
    still assembled here, because the production one reads a live SQLite
    install that a benchmark has no business opening.
    """
    parts = base_system_sections(case.prompt, with_tools=with_tools)
    if case.memory:
        parts.append("\nContexto recordado sobre o utilizador:\n"
                     + "\n".join(f"- {fact}" for fact in case.memory) + "\n")
    return "".join(parts)


def messages_for(case: Case, system: str) -> list[dict]:
    return [{"role": "system", "content": system}, *case.history,
            {"role": "user", "content": case.prompt}]


def tools_for(case: Case, all_tools: list[dict]) -> list[dict]:
    """The tool subset Nano would really offer for this message."""
    task = model_selection.classify(case.prompt)
    return model_selection.select_tools(case.prompt, all_tools, task=task)


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class Budget:
    """A hard request ceiling. Spent budget stops the model, not a warning."""

    def __init__(self, limit: int):
        self.limit = int(limit)
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


async def run_google(model: str, case: Case, tools: list[dict],
                     records: dict[str, dict]) -> Result:
    key = google_provider.google_api_key()
    result = Result(case.case_id if hasattr(case, "case_id") else case.id,
                    case.category, "google", model, ok=False)
    if not key:
        result.error = "no_api_key"
        return result

    system = system_prompt_for(case, bool(tools))
    body = google_provider.build_request(
        model, messages_for(case, system), tools,
        max_tokens=model_selection.max_tokens_for(model_selection.classify(case.prompt)),
        task=model_selection.classify(case.prompt).value,
        metadata=records.get(model))

    chat = google_provider.GoogleChat(key)
    collector: dict = {}
    started = time.monotonic()
    pieces: list[str] = []
    try:
        async for piece in chat.stream(model, body, collector):
            pieces.append(piece)
            collector["chunks"] = collector.get("chunks", 0) + 1
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        from core.provider_failures import classify

        failure = classify(exc, provider="google")
        result.error = failure.message[:300]
        result.failure_type = failure.type.value
        result.total_ms = int((time.monotonic() - started) * 1000)
        return result

    return _finish(result, collector, pieces, started, case)


async def run_groq(model: str, case: Case, tools: list[dict]) -> Result:
    from groq import AsyncGroq

    result = Result(case.id, case.category, "groq", model, ok=False)
    key = secret_store.get_secret(providers.GROQ_SECRET_NAME)
    if not key:
        result.error = "no_api_key"
        return result

    system = system_prompt_for(case, bool(tools))
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages_for(case, system),
        "temperature": 0.65,
        "max_tokens": model_selection.max_tokens_for(model_selection.classify(case.prompt)),
        "stream": True,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    client = AsyncGroq(api_key=key, max_retries=0)
    collector: dict = {"tool_calls": []}
    pieces: list[str] = []
    acc: dict[int, dict] = {}
    started = time.monotonic()
    try:
        response = await client.chat.completions.create(**kwargs)
        async for chunk in response:
            choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
            delta = getattr(choice, "delta", None) if choice else None
            if delta is None:
                continue
            if getattr(delta, "content", None):
                if collector.get("first_token_at") is None:
                    collector["first_token_at"] = time.monotonic()
                pieces.append(delta.content)
                collector["chunks"] = collector.get("chunks", 0) + 1
            for call in (getattr(delta, "tool_calls", None) or []):
                slot = acc.setdefault(int(getattr(call, "index", 0) or 0),
                                      {"id": "", "name": "", "args": ""})
                function = getattr(call, "function", None)
                if function is not None:
                    if getattr(function, "name", None):
                        slot["name"] = function.name
                    if getattr(function, "arguments", None):
                        slot["args"] += function.arguments
            usage = getattr(getattr(chunk, "x_groq", None), "usage", None)
            if usage is not None:
                collector["usage"] = {
                    "prompt_tokens": getattr(usage, "prompt_tokens", None),
                    "completion_tokens": getattr(usage, "completion_tokens", None),
                }
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        from core.provider_failures import classify

        failure = classify(exc, provider="groq")
        result.error = failure.message[:300]
        result.failure_type = failure.type.value
        result.total_ms = int((time.monotonic() - started) * 1000)
        return result

    collector["tool_calls"] = [acc[k] for k in sorted(acc) if acc[k].get("name")]
    return _finish(result, collector, pieces, started, case)


async def run_mistral(model: str, case: Case, tools: list[dict],
                      records: dict[str, dict]) -> Result:
    """One case against one Mistral model, through the PRODUCTION adapter.

    ``mistral_provider.build_request`` and ``MistralChat.stream`` are the same
    two functions ``Brain._mistral_round`` calls, so what is measured here is
    what production sends -- including the tool-call id rewrite. A benchmark
    that assembled its own request would be measuring a request Nano never
    makes, which is the drift ``--self-check`` exists to catch on the prompt.
    """
    result = Result(case.id, case.category, "mistral", model, ok=False)
    key = mistral_provider.mistral_api_key()
    if not key:
        result.error = "no_api_key"
        return result

    system = system_prompt_for(case, bool(tools))
    task = model_selection.classify(case.prompt)
    body = mistral_provider.build_request(
        model, messages_for(case, system), tools,
        max_tokens=model_selection.max_tokens_for(task),
        task=task.value, metadata=records.get(model))

    chat = mistral_provider.MistralChat(key)
    collector: dict = {}
    started = time.monotonic()
    pieces: list[str] = []
    try:
        async for piece in chat.stream(model, body, collector):
            pieces.append(piece)
            collector["chunks"] = collector.get("chunks", 0) + 1
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        from core.provider_failures import classify

        failure = classify(exc, provider="mistral")
        result.error = failure.message[:300]
        result.failure_type = failure.type.value
        result.total_ms = int((time.monotonic() - started) * 1000)
        return result

    return _finish(result, collector, pieces, started, case)


def _finish(result: Result, collector: dict, pieces: list[str],
            started: float, case: Case) -> Result:
    """Score one completed exchange. Shared so both providers grade identically."""
    answer = "".join(pieces)
    total = time.monotonic() - started
    result.total_ms = int(total * 1000)
    first = collector.get("first_token_at")
    if first is not None:
        result.first_token_ms = int((first - started) * 1000)
        generation = total - (first - started)
        # THROUGHPUT IS NOT ALWAYS MEASURABLE, AND SAYING SO IS THE POINT.
        #
        # When a whole short answer arrives in one delta the generation window
        # is a rounding error, and len/window produced 7 000 000 chars/second
        # in the first real run. A number that large is not a fast model, it is
        # a fabricated measurement -- so the field stays None and the report
        # prints an em dash. Nano does not display a state it has not measured,
        # and neither does its benchmark.
        if generation >= MIN_THROUGHPUT_WINDOW_SECONDS and collector.get("chunks", 0) >= 2:
            result.chars_per_second = round(len(answer) / generation, 1)

    calls: list[dict] = []
    for call in collector.get("tool_calls") or []:
        raw = call.get("args") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (ValueError, TypeError):
            args = {"__unparseable__": str(raw)[:200]}
        calls.append({"name": str(call.get("name") or ""), "args": args})

    usage = collector.get("usage") or {}
    result.prompt_tokens = usage.get("prompt_tokens")
    result.completion_tokens = usage.get("completion_tokens")

    graded = grade(case, answer, calls)
    result.ok = graded.passed
    result.verdict = graded.verdict
    result.checks = graded.checks
    result.reasons = graded.reasons
    result.needs_review = graded.needs_review
    result.answer = answer[:2000]
    result.tool_calls = calls
    return result


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

async def run_model(spec: str, cases: list[Case], budget: Budget,
                    records: dict[str, dict[str, dict]], pause: float) -> list[Result]:
    provider_id, _, model = spec.partition(":")
    provider_id = provider_id.strip().lower()
    model = model.strip()
    results: list[Result] = []

    for case in cases:
        if not budget.take():
            print(f"  ! budget spent for {spec} after {budget.used} request(s); stopping")
            break
        tools = tools_for(case, ALL_TOOLS)
        if provider_id == "google":
            result = await run_google(model, case, tools, records.get("google", {}))
        elif provider_id == "groq":
            result = await run_groq(model, case, tools)
        elif provider_id == "mistral":
            result = await run_mistral(model, case, tools, records.get("mistral", {}))
        else:
            raise SystemExit(f"unknown provider in --models: {spec!r}")
        results.append(result)

        mark = {"PASS": "ok ", "FAIL": "no ", "REVIEW": "?  ",
                "UNMEASURED": "ERR"}.get(result.verdict, "ERR")
        extra = f" [{result.failure_type}]" if result.failure_type else ""
        review = " (por rever)" if result.verdict == VERDICT_REVIEW else ""
        print(f"  {mark} {case.id:8} {result.total_ms or 0:6} ms{extra}{review}")

        if result.failure_type == "RATE_LIMIT":
            print(f"  ! {spec} is rate-limited; stopping this model to protect the quota")
            break
        if pause:
            await asyncio.sleep(pause)

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarise(results: list[Result]) -> dict[str, Any]:
    if not results:
        return {}
    graded = [r for r in results if r.error is None]
    latencies = [r.first_token_ms for r in graded if r.first_token_ms is not None]
    totals = [r.total_ms for r in graded if r.total_ms is not None]

    def _median(values: list[int]) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        middle = len(ordered) // 2
        return (ordered[middle] if len(ordered) % 2
                else (ordered[middle - 1] + ordered[middle]) // 2)

    tool_cases = [r for r in graded if "expected_tool" in r.checks]
    forbidden = [r for r in graded if r.checks.get("forbidden_tool") is False]
    portuguese = [r for r in graded if "portuguese" in r.checks]
    memory = [r for r in graded if r.category == "MEMORY"]

    # SECURITY IS COUNTED OVER VERDICTS, NOT OVER ``ok``, AND A REQUEST THAT
    # NEVER LANDED IS NOT A PASS.
    #
    # ``graded`` already excludes errored requests, so a provider that failed
    # on a security case used to shrink the denominator and leave the
    # percentage looking healthy. Both exclusions are now REPORTED instead of
    # being invisible: ``security_review`` is what a human still has to read
    # and ``security_unmeasured`` is what the provider never answered.
    all_security = [r for r in results if r.category == "SECURITY"]
    security_decided = [r for r in all_security
                        if r.verdict in (VERDICT_PASS, VERDICT_FAIL)]
    security_review = [r for r in all_security if r.verdict == VERDICT_REVIEW]
    security_unmeasured = [r for r in all_security if r.error is not None]

    def _rate(rows: list[Result]) -> float | None:
        return round(100 * sum(1 for r in rows if r.ok) / len(rows), 1) if rows else None

    return {
        "requests": len(results),
        "errors": len(results) - len(graded),
        "rate_limited": sum(1 for r in results if r.failure_type == "RATE_LIMIT"),
        "passed": sum(1 for r in graded if r.ok),
        "pass_rate": _rate(graded),
        "median_first_token_ms": _median(latencies),
        "median_total_ms": _median(totals),
        "median_chars_per_second": (
            round(sorted(r.chars_per_second for r in graded if r.chars_per_second)
                  [len([r for r in graded if r.chars_per_second]) // 2], 1)
            if any(r.chars_per_second for r in graded) else None),
        "tool_accuracy": (round(100 * sum(1 for r in tool_cases
                                          if r.checks.get("expected_tool")) / len(tool_cases), 1)
                          if tool_cases else None),
        "forbidden_tool_calls": len(forbidden),
        "portuguese_rate": (round(100 * sum(1 for r in portuguese
                                            if r.checks.get("portuguese")) / len(portuguese), 1)
                            if portuguese else None),
        "memory_pass_rate": _rate(memory),
        # Only decided cases. None when every security case is REVIEW or
        # UNMEASURED -- an em dash is the honest reading, not 100%.
        "security_pass_rate": (
            round(100 * sum(1 for r in security_decided if r.verdict == VERDICT_PASS)
                  / len(security_decided), 1) if security_decided else None),
        "security_failed": sum(1 for r in all_security if r.verdict == VERDICT_FAIL),
        "security_review": len(security_review),
        "security_unmeasured": len(security_unmeasured),
        "needs_review": sum(1 for r in graded if r.needs_review),
        "prompt_tokens": sum(r.prompt_tokens or 0 for r in graded) or None,
        "completion_tokens": sum(r.completion_tokens or 0 for r in graded) or None,
    }


SECRET_PATTERNS = (
    re.compile(r"\bAIza[0-9A-Za-z_\-]{10,}"),
    re.compile(r"\bgsk_[0-9A-Za-z]{10,}"),
    re.compile(r"x-goog-api-key"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{12,}"),
    # Mistral keys are a bare 32-character alphanumeric string with no vendor
    # prefix to match on, so the pattern is anchored on the assignment shapes a
    # leaked one would appear in rather than on the key itself. A blind
    # 32-character rule would match ordinary model output and refuse to write
    # perfectly clean reports, and a check that cries wolf gets deleted.
    re.compile(r"(?i)mistral[_-]?api[_-]?key\s*[:=]\s*[\"']?[A-Za-z0-9]{16,}"),
)


def assert_no_secret(blob: str) -> None:
    """Refuse to write a report that contains a credential.

    The report holds raw model output, and a model asked "show me my API key"
    could in principle echo something key-shaped back. Checking here costs
    nothing and the alternative is a secret in a file the user may well share.
    """
    for pattern in SECRET_PATTERNS:
        if pattern.search(blob):
            raise SystemExit("REFUSING TO WRITE: the report matched a credential pattern "
                             f"({pattern.pattern}). Nothing was saved.")


def write_report(phase: str, by_model: dict[str, list[Result]]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {
        "phase": phase,
        "generated_at": stamp,
        "summary": {spec: summarise(rows) for spec, rows in by_model.items()},
        "results": {spec: [asdict(r) for r in rows] for spec, rows in by_model.items()},
    }
    blob = json.dumps(payload, ensure_ascii=False, indent=2)
    assert_no_secret(blob)
    path = OUTPUT_DIR / f"benchmark-{phase}-{stamp}.json"
    path.write_text(blob, encoding="utf-8")
    return path


COLUMNS = [
    ("requests", "pedidos"), ("pass_rate", "aprovação %"),
    ("median_first_token_ms", "1º token ms"), ("median_total_ms", "total ms"),
    ("median_chars_per_second", "car/s"),
    ("tool_accuracy", "ferramenta %"), ("forbidden_tool_calls", "proibidas"),
    ("portuguese_rate", "pt-PT %"), ("memory_pass_rate", "memória %"),
    ("security_pass_rate", "segurança %"),
    ("security_failed", "seg. falhas"), ("security_review", "seg. rever"),
    ("errors", "erros"), ("rate_limited", "429"),
    ("prompt_tokens", "tok in"), ("completion_tokens", "tok out"),
    ("needs_review", "p/ rever"),
]


def print_table(by_model: dict[str, list[Result]]) -> None:
    summaries = {spec: summarise(rows) for spec, rows in by_model.items()}
    width = max((len(s) for s in summaries), default=10)
    header = f"{'modelo'.ljust(width)} | " + " | ".join(label for _, label in COLUMNS)
    print("\n" + header)
    print("-" * len(header))
    for spec, summary in summaries.items():
        cells = []
        for key, label in COLUMNS:
            value = summary.get(key)
            cells.append(("—" if value is None else str(value)).rjust(len(label)))
        print(f"{spec.ljust(width)} | " + " | ".join(cells))
    print()


# ---------------------------------------------------------------------------
# Self-check
# ---------------------------------------------------------------------------

def _prompt_parity_problems() -> list[str]:
    """Check the measured prompt still IS the production prompt.

    ``system_prompt_for`` delegates to ``core.brain.base_system_sections``, and
    the point of this check is that it keeps doing so. The failure it exists
    for was silent: the benchmark used to build the head of the prompt itself
    and had stopped appending the capability-grounding block, so every SECURITY
    case was answered by a model that had never been told Nano has no shell,
    and the report said nothing was wrong.

    So the check is behavioural, not a source scan. It assembles a real prompt
    for a real case and asks whether each production section is in it -- which
    is false the moment the delegation is removed, however the replacement is
    spelled.
    """
    problems: list[str] = []
    shell_case = next((c for c in CASES if capabilities.detect(c.prompt)), None)
    if shell_case is None:
        return ["no case asks for an unsupported capability, so the grounding "
                "block can never be exercised"]

    with_tools = system_prompt_for(shell_case, True)
    without_tools = system_prompt_for(shell_case, False)
    expected = brain.base_system_sections(shell_case.prompt, with_tools=True)

    for section in expected:
        if section not in with_tools:
            problems.append("the benchmark prompt is missing a production "
                            f"section that starts {section.strip()[:60]!r}")

    grounding = capabilities.grounding_block(shell_case.prompt)
    if not grounding:
        problems.append(f"{shell_case.id} no longer matches a declared "
                        "unsupported capability")
    else:
        for name, prompt in (("with tools", with_tools),
                             ("without tools", without_tools)):
            if grounding not in prompt:
                problems.append(f"capabilities grounding is absent from the "
                                f"{name} prompt")

    for label, fragment in (("tool rules", brain.NANO_TOOL_RULES),
                            ("trust boundary", TRUST_BOUNDARY_SYSTEM_RULES)):
        if fragment not in with_tools:
            problems.append(f"{label} absent from the tool-bearing prompt")
    return problems


def self_check() -> int:
    """Prove the benchmark cannot act on the machine, without a network call.

    Two independent facts, both checked against the parsed source rather than
    against a promise in a docstring.
    """
    import ast

    def executable_symbols(path: Path) -> tuple[set[str], set[str]]:
        """Every NAME and ATTRIBUTE the module really executes.

        Comments and docstrings are excluded, and string literals are never
        inspected. Both files explain at length that they never reach
        ToolExecutor, so a scan that matches text would report the explanation
        as the hazard -- the trap that has caught this repository four times.
        Reading the parsed tree instead means a reference only counts when it
        is something the interpreter would resolve.
        """
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add((node.module or "").split(".")[0])
                imports.update(alias.name for alias in node.names)
        return names | imports, attrs

    #: Anything that could reach the operating system, or the execution
    #: authority that owns it. None of these may be resolvable from either file.
    #:
    #: The MODULES are what makes this precise: forbidding a bare name like
    #: "system" would match the local variable holding a system prompt, and a
    #: check that cries wolf gets deleted. Neither file imports os or
    #: subprocess, so os.system, os.startfile and Popen are unreachable by
    #: construction rather than by blacklist.
    FORBIDDEN = ("os", "subprocess", "ctypes", "shutil",
                 "ToolExecutor", "PermissionManager", "PolicyEngine",
                 "execute_tool_async", "execute_tool", "_run_tool",
                 "Popen", "startfile")

    problems = []
    for path in (Path(__file__), ROOT / "scripts" / "benchmark_cases.py"):
        names, attrs = executable_symbols(path)
        for forbidden in FORBIDDEN:
            if forbidden in names or forbidden in attrs:
                problems.append(f"{path.name} can resolve {forbidden}")

    print(f"corpus: {len(CASES)} cases, {len(phase_a_cases())} in phase A")
    categories = sorted({c.category for c in CASES})
    for category in categories:
        rows = [c for c in CASES if c.category == category]
        print(f"  {category:16} {len(rows):3} cases")

    missing = sorted({name for c in CASES for name in expected_tools(c)
                      if name not in ADVERTISED})
    if missing:
        problems.append(f"cases expect tools that do not exist: {missing}")

    problems.extend(_prompt_parity_problems())

    for problem in problems:
        print(f"FAIL  {problem}")
    if not problems:
        print("\nOK    no execution path from the benchmark into ToolExecutor")
        print("OK    every expected tool exists in the live registry")
        print("OK    the prompt matches production, grounding block included")
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def list_models() -> int:
    records, error = google_provider.list_google_models()
    if error:
        print(f"Google: {error}")
    else:
        print(f"Google ({len(records)} chat models):")
        for record in records:
            flags = []
            if record.get("tool_calling") is False:
                flags.append("sem ferramentas")
            if record.get("thinking"):
                flags.append("raciocínio")
            if record.get("input_tokens"):
                flags.append(f"ctx {record['input_tokens']}")
            print(f"  google:{record['id']:45} {record['display_name']}"
                  + (f"   [{', '.join(flags)}]" if flags else ""))

    groq_models, groq_error = providers.list_groq_models()
    if groq_error:
        print(f"Groq: {groq_error}")
    else:
        print(f"\nGroq ({len(groq_models)} chat models):")
        for model in groq_models:
            print(f"  groq:{model}")

    mistral_records, mistral_error = mistral_provider.list_mistral_models()
    if mistral_error:
        print(f"\nMistral: {mistral_error}")
    else:
        print(f"\nMistral ({len(mistral_records)} chat models):")
        for record in mistral_records:
            flags = []
            if record.get("tool_calling") is False:
                flags.append("sem ferramentas")
            if record.get("vision"):
                flags.append("visão")
            if record.get("deprecated"):
                flags.append("descontinuado")
            if record.get("input_tokens"):
                flags.append(f"ctx {record['input_tokens']}")
            print(f"  mistral:{record['id']:45} {record['display_name']}"
                  + (f"   [{', '.join(flags)}]" if flags else ""))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", choices=["a", "b", "c"], default="a",
                        help="a = screening subset, b/c = full corpus")
    parser.add_argument("--models", default="",
                        help="comma-separated provider:model_id (ids come from --list-models)")
    parser.add_argument("--categories", default="",
                        help="restrict to these categories (phase b/c only)")
    parser.add_argument("--cases", default="",
                        help="comma-separated case ids, run in the order given; "
                             "overrides --phase/--categories selection")
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                        help=f"max requests per model (default {DEFAULT_BUDGET})")
    parser.add_argument("--low-quota", default="",
                        help=f"comma-separated specs capped at {LOW_QUOTA_BUDGET} requests")
    parser.add_argument("--pause", type=float, default=1.5,
                        help="seconds between requests, to respect requests-per-minute limits")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()

    load_all_plugins()
    global ALL_TOOLS, ADVERTISED
    ALL_TOOLS = get_all_tools()
    ADVERTISED = {(t.get("function") or {}).get("name") for t in ALL_TOOLS}

    if args.self_check:
        return self_check()
    if args.list_models:
        return list_models()

    specs = [s.strip() for s in args.models.split(",") if s.strip()]
    if not specs:
        parser.error("--models is required; run --list-models to see the real ids")

    low_quota = {s.strip() for s in args.low_quota.split(",") if s.strip()}
    chosen = [c.strip() for c in args.cases.split(",") if c.strip()]
    if chosen:
        cases = cases_by_id(chosen)
    else:
        cases = (phase_a_cases() if args.phase == "a"
                 else cases_for([c.strip() for c in args.categories.split(",") if c.strip()]))

    # Per-model discovery metadata, fetched ONLY for the providers actually
    # named in --models. A benchmark that probed every account would spend a
    # request against a provider the user did not ask about, and free quotas
    # are exactly what the budget machinery exists to protect.
    by_id: dict[str, dict[str, dict]] = {}
    if any(spec.startswith("google:") for spec in specs):
        records, error = google_provider.list_google_models()
        by_id["google"] = {r["id"]: r for r in records} if not error else {}
        if error:
            print(f"! Google model metadata unavailable ({error}); "
                  "reasoning configuration will be omitted.")
    if any(spec.startswith("mistral:") for spec in specs):
        records, error = mistral_provider.list_mistral_models()
        by_id["mistral"] = {r["id"]: r for r in records} if not error else {}
        if error:
            print(f"! Mistral model metadata unavailable ({error}); "
                  "tool-calling capability will be assumed present.")

    print(f"\nPhase {args.phase.upper()}: {len(cases)} case(s) x {len(specs)} model(s)")
    by_model: dict[str, list[Result]] = {}
    for spec in specs:
        limit = LOW_QUOTA_BUDGET if spec in low_quota else args.budget
        budget = Budget(min(limit, len(cases)))
        print(f"\n{spec}  (budget {budget.limit} request(s))")
        by_model[spec] = asyncio.run(
            run_model(spec, cases, budget, by_id, args.pause))

    print_table(by_model)
    path = write_report(args.phase, by_model)
    print(f"Report: {path}")
    print("Review the answers marked (review) by hand; they are not computable.")
    return 0


ALL_TOOLS: list[dict] = []
ADVERTISED: set[str] = set()

if __name__ == "__main__":
    raise SystemExit(main())
