"""Runs a classifier over the golden tickets, without running the agent.

Classifying is the first model call of a ticket and needs nothing but the message. So a
different classifier can be scored against the 120 labels in about a minute, before an
overnight run is spent on it. Nothing here touches the database or the MCP server.

It prints the things a full run would otherwise be the first to show:

    intent accuracy against the labels, overall and per category
    every ticket it gets wrong
    every ticket under the confidence floor, which the graph sends straight to a person
    against a results file, which tickets it fixes and which it breaks compared with that run
    forbidden tools that the allowlist would newly let through for the intent it picked

The message is redacted first, exactly as the graph does it. The classifier is the one set in
.env. If a decider cannot be reached the agent's own model stands in, and those tickets are
counted apart, because they say nothing about the classifier being measured.

Nothing from a replay goes in the tables. It is the reason to spend a full run, or not to.
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.context import RunContext
from agent.guardrails.policy import CONFIDENCE_FLOOR, TOOL_ALLOWLIST, escalation_signals
from agent.guardrails.redact import redact
from agent.nodes.classify import classify_message, named
from agent.providers import get_chat_model, get_classifier
from evals.metrics import percentile
from evals.run import load_cases
from evals.schema import CATEGORY_TAGS, GoldenCase

log = logging.getLogger("evals.classifier_replay")

FLOORS = (0.4, 0.5, 0.6, 0.7, 0.8)
INTENT_GATE = 0.90


def classify_case(classifier, case: GoldenCase) -> dict:
    ctx = RunContext(classify_model=classifier)
    started = time.perf_counter()
    row = {"case_id": case.case_id, "category": case.category, "expected_intent": case.expected.intent,
           "expected_decision": case.expected.decision, "must_escalate": case.expected.must_escalate}
    try:
        result, record = classify_message(redact(case.raw_message)[0], case.channel, ctx)
    except Exception as exc:
        return {**row, "error": f"{type(exc).__name__}: {str(exc)[:200]}", "intent": None, "confidence": None,
                "fell_back": False, "latency_ms": round((time.perf_counter() - started) * 1000), "cost_inr": 0.0}
    return {**row, "error": None, "intent": result.intent, "confidence": round(result.confidence, 3),
            "urgency": result.urgency, "sentiment": result.sentiment, "order_id": result.order_id,
            "intent_probabilities": record["intent_probabilities"], "classified_by": record["classified_by"],
            "fell_back": record["classifier_fell_back"], "cost_inr": ctx.usage.cost_inr,
            "latency_ms": round((time.perf_counter() - started) * 1000)}


def right(row: dict) -> bool:
    return row["intent"] == row["expected_intent"]


def accuracy(rows: list[dict]) -> float | None:
    return round(sum(right(r) for r in rows) / len(rows), 4) if rows else None


def at_floor(rows: list[dict], floor: float) -> dict:
    """What the graph does at this floor: a ticket under it never reaches the plan."""
    under = [r for r in rows if r["confidence"] is not None and r["confidence"] < floor]
    return {"under": len(under), "under_and_wrong": sum(not right(r) for r in under),
            "under_and_must_escalate": sum(r["must_escalate"] for r in under),
            "under_and_should_be_handled": sum(not r["must_escalate"] for r in under)}


def reachable(case: GoldenCase, intent: str | None) -> set[str]:
    """Tools the label forbids that the allowlist would let through for this intent. A message
    with an escalation signal reaches none, since the guardrail refuses every change on it."""
    if escalation_signals(case.raw_message):
        return set()
    return set(case.expected.forbidden_tools) & TOOL_ALLOWLIST.get(intent, set())


def against_recorded(rows: list[dict], cases: dict[str, GoldenCase], results: dict) -> dict:
    recorded = {r["case_id"]: r for r in results["cases"]}
    both = [r for r in rows if r["case_id"] in recorded and not r["error"]]
    fixed, broken, opened, closed = [], [], [], []
    for r in both:
        was = recorded[r["case_id"]]["predicted"]
        was_right = was["intent"] == r["expected_intent"]
        note = {"case_id": r["case_id"], "expected": r["expected_intent"], "recorded": was["intent"], "now": r["intent"],
                "confidence": r["confidence"], "recorded_outcome": was.get("terminal_reason")}
        if right(r) and not was_right:
            fixed.append(note)
        if was_right and not right(r):
            broken.append(note)
        before, after = reachable(cases[r["case_id"]], was["intent"]), reachable(cases[r["case_id"]], r["intent"])
        if after - before:
            opened.append({**note, "tools": sorted(after - before)})
        if before - after:
            closed.append({**note, "tools": sorted(before - after)})
    return {"tickets": len(both),
            "recorded_accuracy": round(sum(recorded[r["case_id"]]["predicted"]["intent"] == r["expected_intent"] for r in both) / len(both), 4)
            if both else None,
            "same_intent": sum(recorded[r["case_id"]]["predicted"]["intent"] == r["intent"] for r in both),
            "fixed": fixed, "broken": broken, "allowlist_opened": opened, "allowlist_closed": closed}


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"]]
    measured = [r for r in ok if not r["fell_back"]]
    latencies = [r["latency_ms"] for r in measured]
    return {
        "tickets": len(rows),
        "errors": len(rows) - len(ok),
        "fell_back": len(ok) - len(measured),
        # A ticket that could not be classified counts as wrong, the same as in a full run.
        "intent_accuracy": accuracy(rows),
        "by_category": {tag: accuracy([r for r in rows if r["category"] == tag]) for tag in CATEGORY_TAGS},
        "confusions": [[f"{a} read as {b}", n] for (a, b), n in
                       Counter((r["expected_intent"], r["intent"]) for r in ok if not right(r)).most_common()],
        "confidence_p50": percentile([r["confidence"] for r in ok], 50),
        "by_floor": {str(f): at_floor(ok, f) for f in FLOORS},
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "cost_inr": round(sum(r["cost_inr"] for r in ok), 4),
    }


def show(summary: dict, rows: list[dict], compared: dict | None, limit: int) -> None:
    wrong = [r for r in rows if not r["error"] and not right(r)]
    print(f"\n  Intent accuracy {summary['intent_accuracy']} on {summary['tickets']} tickets, {len(wrong)} wrong. "
          f"The gate needs {INTENT_GATE:.2f}.")
    print("  " + ", ".join(f"{tag} {value}" for tag, value in summary["by_category"].items() if value is not None))
    for r in wrong[:limit]:
        print(f"    {r['case_id']}  {r['expected_intent']} read as {r['intent']}  confidence {r['confidence']}")

    floor = summary["by_floor"][str(CONFIDENCE_FLOOR)]
    print(f"\n  Confidence: median {summary['confidence_p50']}. Under the floor of {CONFIDENCE_FLOOR}: {floor['under']} tickets, "
          f"which go straight to a person.")
    print(f"    {floor['under_and_must_escalate']} of them had to escalate anyway, "
          f"{floor['under_and_should_be_handled']} should have been answered or acted on, {floor['under_and_wrong']} had the wrong intent.")
    for value, numbers in summary["by_floor"].items():
        print(f"    floor {value}: {numbers['under']} under, {numbers['under_and_should_be_handled']} of those lost to a person, "
              f"{numbers['under_and_wrong']} of those wrong anyway")

    if compared:
        print(f"\n  Against the recorded run: {compared['tickets']} tickets in both, recorded accuracy {compared['recorded_accuracy']}, "
              f"same intent on {compared['same_intent']}")
        print(f"  Fixed, wrong in the recorded run and right now: {len(compared['fixed'])}")
        for n in compared["fixed"][:limit]:
            print(f"    {n['case_id']}  {n['recorded']} is now {n['now']}  the recorded run ended {n['recorded_outcome']}")
        print(f"  Broken, right in the recorded run and wrong now: {len(compared['broken'])}")
        for n in compared["broken"][:limit]:
            print(f"    {n['case_id']}  {n['expected']} is now {n['now']}  the recorded run ended {n['recorded_outcome']}")
        print(f"  Forbidden tools the allowlist newly lets through: {len(compared['allowlist_opened'])} tickets")
        for n in compared["allowlist_opened"]:
            print(f"    {n['case_id']}  {', '.join(n['tools'])} as {n['now']}. The policy rules and the plan are what is left.")
        print(f"  Forbidden tools the allowlist now shuts out: {len(compared['allowlist_closed'])} tickets")
        for n in compared["allowlist_closed"]:
            print(f"    {n['case_id']}  {', '.join(n['tools'])}, no longer reachable as {n['now']}")

    print(f"\n  classifier latency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms, "
          f"cost Rs {summary['cost_inr']:.2f} for the whole replay")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score a classifier against the golden labels without running the agent.")
    parser.add_argument("results", type=Path, nargs="?", help="a recorded results file to compare with, for example results_v6.json")
    parser.add_argument("--subset", choices=["smoke", "full", "adversarial", "regression"], default="full")
    parser.add_argument("--case", action="append", help="classify only this case id, can be repeated")
    parser.add_argument("--classifier", help="overrides DEFLECT_CLASSIFIER_PROVIDER for this replay. agent means the agent's own model")
    parser.add_argument("--workers", type=int, default=4, help="keep 1 when the classifier is a local model")
    parser.add_argument("--show", type=int, default=30, help="how many tickets of each kind to print")
    parser.add_argument("--out", type=Path, default=Path("classifier_replay.json"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    if args.classifier:
        os.environ["DEFLECT_CLASSIFIER_PROVIDER"] = "" if args.classifier == "agent" else args.classifier
        os.environ["DEFLECT_CLASSIFIER_MODEL"] = ""
    try:
        classifier = get_classifier(get_chat_model("agent"))
    except (ImportError, ValueError) as exc:
        print(f"Cannot start the classifier: {exc}", file=sys.stderr)
        return 2
    recorded = json.loads(args.results.read_text(encoding="utf-8")) if args.results else None

    cases = load_cases(args.subset, args.case)
    print(f"Classifying {len(cases)} tickets with {named(classifier)}")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(lambda c: classify_case(classifier, c), cases))

    summary = summarise(rows)
    compared = against_recorded(rows, {c.case_id: c for c in cases}, recorded) if recorded else None
    args.out.write_text(json.dumps({
        "classifier": named(classifier), "subset": args.subset if not args.case else "custom",
        "compared_with": args.results.name if args.results else None, "confidence_floor": CONFIDENCE_FLOOR,
        "summary": summary, "against_recorded": compared, "tickets": rows}, indent=2), encoding="utf-8")

    if summary["errors"]:
        first = next(r["error"] for r in rows if r["error"])
        print(f"\n  {summary['errors']} tickets could not be classified and count as wrong, the first error was: {first}", file=sys.stderr)
    if summary["fell_back"]:
        print(f"\n  {summary['fell_back']} tickets were classified by the agent's own model because {named(classifier)} "
              "could not be reached. They say nothing about this classifier.", file=sys.stderr)
    show(summary, rows, compared, args.show)
    print(f"\nWrote {args.out}. Nothing from a replay goes in the tables.")
    return 1 if summary["errors"] == len(rows) else 0


if __name__ == "__main__":
    sys.exit(main())
