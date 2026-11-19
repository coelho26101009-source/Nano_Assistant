"""Fold benchmark runs into the committed routing artifact.

WHY THE ARTIFACT IS NOT THE REPORT
----------------------------------
``scripts/benchmark_providers.py`` writes full reports to
``runtime/benchmarks/``, and that directory is gitignored on purpose: the
reports contain VERBATIM MODEL OUTPUT, including whatever a model said when
asked "mostra-me a minha chave da API". Committing them would put raw provider
text -- and, on a bad day, a credential a model echoed -- into the repository
for ever.

The routing decision still has to be defensible from inside the repository, or
it is a preference wearing a benchmark's clothes. So this script keeps exactly
the part that defends it -- the verdicts, the checks that produced them, the
latencies, the failures -- and drops every answer and every tool argument.

WHAT IS DELIBERATELY DROPPED
    answer                the model's own words
    tool_calls[].args     arguments, which echo the prompt and can carry paths
    any credential        enforced, not promised: see assert_no_secret

WHAT IS KEPT
    the corpus            synthetic prompts written for this project, with the
                          expectations that grade them
    per-case verdicts     PASS / FAIL / REVIEW / UNMEASURED, and WHY
    latencies             per case, so a median can be recomputed
    failures              classified, so "it was rate-limited" stays visible

MERGING RULES
    A case measured in one run and rate-limited in another counts as measured:
    the quota stopped the benchmark, not the model. A later measurement of the
    same case replaces an earlier one, so re-running a category updates it.

    Usage:
        python scripts/export_routing_benchmark.py                  # today
        python scripts/export_routing_benchmark.py --stamp 20260909
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.benchmark_cases import CASES                             # noqa: E402
from scripts.benchmark_providers import assert_no_secret              # noqa: E402

REPORTS = ROOT / "runtime" / "benchmarks"
ARTIFACT = ROOT / "benchmarks" / "provider_routing"

#: Bumped whenever a case is added, removed or reworded. A result set carries
#: the version it was measured against, so a stale artifact is visible rather
#: than merely old.
CORPUS_VERSION = "1.0.0"


def corpus() -> dict:
    """The prompts and their expectations. Synthetic, and free of user data.

    Every prompt here was written for this benchmark. None came from a real
    conversation, and the "memory" facts are invented for the same reason: the
    user's own database and history are never mined for test material.
    """
    return {
        "corpus_version": CORPUS_VERSION,
        "case_count": len(CASES),
        "categories": sorted({c.category for c in CASES}),
        "cases": [
            {
                "id": c.id,
                "category": c.category,
                "prompt": c.prompt,
                "history": c.history,
                "injected_memory": c.memory,
                "expect_tool": c.expect_tool,
                "expect_args": c.expect_args,
                "forbid_tools": c.forbid_tools,
                "must_contain": c.must_contain,
                "must_not_contain": c.must_not_contain,
                "max_words": c.max_words,
                "forbid_execution_offer": c.forbid_execution_offer,
                "forbid_executable_help": c.forbid_executable_help,
                "forbid_bypass_agreement": c.forbid_bypass_agreement,
                "review": c.review,
            }
            for c in CASES
        ],
    }


def _merge(stamp: str) -> tuple[dict[str, dict[str, dict]], list[str]]:
    merged: dict[str, dict[str, dict]] = {}
    sources: list[str] = []
    for path in sorted(REPORTS.glob(f"benchmark-*-{stamp}-*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        sources.append(path.name)
        for spec, results in data.get("results", {}).items():
            bucket = merged.setdefault(spec, {})
            for row in results:
                previous = bucket.get(row["case_id"])
                if previous is not None and previous["measured"] and row["error"]:
                    continue                     # a 429 never overwrites a measurement
                bucket[row["case_id"]] = {
                    "case_id": row["case_id"],
                    "category": row["category"],
                    "verdict": row["verdict"],
                    "passed": row["ok"],
                    # The failure CLASS, never the provider's own sentence: a raw
                    # backend error string belongs in a log, not in the repository.
                    "failure_type": row["failure_type"],
                    "measured": row["error"] is None,
                    "first_token_ms": row["first_token_ms"],
                    "total_ms": row["total_ms"],
                    "checks": row["checks"],
                    "reasons": row["reasons"],
                    # Names only. Arguments echo the prompt and can carry paths.
                    "tools_called": [call["name"] for call in row["tool_calls"]],
                    "prompt_tokens": row["prompt_tokens"],
                    "completion_tokens": row["completion_tokens"],
                }
    return merged, sources


def _median(values) -> int | None:
    kept = [v for v in values if v is not None]
    return int(statistics.median(kept)) if kept else None


def _pct(hits: int, total: int) -> float | None:
    return round(100.0 * hits / total, 1) if total else None


def summarise(records: list[dict]) -> dict:
    measured = [r for r in records if r["measured"]]
    tool_cases = [r for r in measured if "expected_tool" in r["checks"]]
    pt_cases = [r for r in measured if "portuguese" in r["checks"]]
    security = [r for r in records if r["category"] == "SECURITY"]
    decided = [r for r in security if r["verdict"] in ("PASS", "FAIL")]
    return {
        "cases_attempted": len(records),
        "cases_measured": len(measured),
        "cases_unmeasured": len(records) - len(measured),
        "rate_limit_events": sum(1 for r in records if r["failure_type"] == "RATE_LIMIT"),
        "pass_rate": _pct(sum(1 for r in measured if r["passed"]), len(measured)),
        "tool_cases": len(tool_cases),
        "tool_accuracy": _pct(sum(1 for r in tool_cases if r["checks"].get("expected_tool")),
                              len(tool_cases)),
        "forbidden_tool_calls": sum(1 for r in measured
                                    if r["checks"].get("forbidden_tool") is False),
        "portuguese_rate": _pct(sum(1 for r in pt_cases if r["checks"].get("portuguese")),
                                len(pt_cases)),
        # Only DECIDED security cases. None when every one is REVIEW or was
        # never measured -- reporting 100% for cases nobody graded is the kind
        # of false precision this artifact exists to avoid.
        "security_decided": len(decided),
        "security_pass_rate": _pct(sum(1 for r in decided if r["verdict"] == "PASS"),
                                   len(decided)),
        "security_failed_cases": sorted(r["case_id"] for r in security
                                        if r["verdict"] == "FAIL"),
        "security_review": sum(1 for r in security if r["verdict"] == "REVIEW"),
        "needs_human_review": sum(1 for r in measured if r["verdict"] == "REVIEW"),
        "median_first_token_ms": _median(r["first_token_ms"] for r in measured),
        "median_total_ms": _median(r["total_ms"] for r in measured),
        "failed_cases": sorted(r["case_id"] for r in records if r["verdict"] == "FAIL"),
        "by_category": {
            category: {
                "measured": len([r for r in measured if r["category"] == category]),
                "pass_rate": _pct(
                    sum(1 for r in measured if r["category"] == category and r["passed"]),
                    len([r for r in measured if r["category"] == category])),
            }
            for category in sorted({r["category"] for r in records})
        },
    }


def build(stamp: str, decision: dict) -> dict:
    merged, sources = _merge(stamp)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "measured_on": stamp,
        "corpus_version": CORPUS_VERSION,
        "environment": {
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "note": ("One Windows workstation, one set of personal free-tier "
                     "accounts, one day. Availability figures below describe "
                     "THOSE accounts and are not a claim about the vendors."),
        },
        "source_reports": sources,
        "models": sorted(merged),
        "summary": {spec: summarise(list(bucket.values()))
                    for spec, bucket in merged.items()},
        "per_case": {spec: sorted(bucket.values(), key=lambda r: r["case_id"])
                     for spec, bucket in merged.items()},
        **decision,
    }


def decision_block() -> dict:
    """The routing conclusion, kept beside the numbers that produced it.

    ``recommended_order`` is asserted against ``providers.CLOUD_PROVIDER_IDS``
    by tests/test_provider_routing_policy.py, so the constant and the evidence
    cannot drift apart silently.
    """
    from core import providers

    return {
        "recommended_order": {
            "cloud": list(providers.CLOUD_PROVIDER_IDS),
            "default_primary": providers.DEFAULT_CLOUD_PROVIDER,
            "terminal": providers.ProviderId.OLLAMA.value,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d"),
                        help="the YYYYMMDD the reports were written under")
    args = parser.parse_args()

    ARTIFACT.mkdir(parents=True, exist_ok=True)
    results = build(args.stamp, decision_block())
    if not results["models"]:
        print(f"no reports found for {args.stamp} under {REPORTS}")
        return 1

    for name, payload in (("benchmark_cases.json", corpus()),
                          ("benchmark_results.json", results)):
        blob = json.dumps(payload, ensure_ascii=False, indent=2)
        assert_no_secret(blob)                   # enforced, not promised
        (ARTIFACT / name).write_text(blob + "\n", encoding="utf-8")
        print(f"wrote {ARTIFACT / name}  ({len(blob):,} bytes)")

    for spec, summary in sorted(results["summary"].items()):
        print(f"  {spec:30} measured={summary['cases_measured']:3}/"
              f"{summary['cases_attempted']:<3} pass={summary['pass_rate']} "
              f"tools={summary['tool_accuracy']} 429s={summary['rate_limit_events']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
