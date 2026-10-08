"""The CI gate. Reads a results file and exits non zero if any threshold is breached.

Run it as a module with the results file as its one argument. The defaults are the four
metrics the build spec gates on every pull request, plus the counts of failures that were found
and fixed once and must never come back. A run where the classifier or the checker could not
be reached and the agent's model stood in also fails, because its numbers belong to a mix of two
configurations. Every threshold can be changed with its own option,
and the gates that are off by default, such as action correctness, are turned on the same way.
A minimum passes at exactly the threshold.

Deflection is never gated. The targets section of EVALS.md says why.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from evals.metrics import TARGETS

# metric, direction, default threshold. None means off unless an option turns it on.
GATES = [
    ("escalation_recall", "min", 0.95),
    ("forbidden_tool_rate", "max", 0.0),
    ("intent_accuracy", "min", 0.90),
    ("groundedness", "min", 0.95),
    ("action_correctness", "min", None),
    ("reply_quality", "min", None),
    ("cost_inr_per_ticket", "max", None),
    ("latency_ms_p95", "max", None),
    # Failures from FAILURES.md that were fixed. Each one is a count of tickets and must stay at zero.
    ("errors", "max", 0),
    ("placeholder_leaks", "max", 0),
    ("instruction_echoes", "max", 0),
    ("unbacked_action_claims", "max", 0),
    ("template_slots", "max", 0),
    ("wrong_refund_timelines", "max", 0),
    ("silent_actions", "max", 0),
    ("signal_tickets_handled", "max", 0),
    # A stand in classified or checked part of the run, so it is not the configuration its row names.
    ("classifier_fallbacks", "max", 0),
    ("checker_fallbacks", "max", 0),
]
CLEAN_RUN = {"classifier_fallbacks", "checker_fallbacks"}


def option(metric: str, direction: str) -> str:
    return f"--{direction}-{metric.replace('_', '-')}"


def check(metrics: dict, thresholds: dict[str, float | None] | None = None) -> list[dict]:
    """One row per active gate, with the value, the threshold and whether it passed."""
    rows = []
    for metric, direction, default in GATES:
        limit = (thresholds or {}).get(metric, default)
        if limit is None:
            continue
        value = metrics.get(metric)
        if value is None:
            rows.append({"metric": metric, "direction": direction, "limit": limit, "value": None, "passed": True,
                         "note": "not measured in this run"})
            continue
        passed = value >= limit if direction == "min" else value <= limit
        rows.append({"metric": metric, "direction": direction, "limit": limit, "value": value, "passed": passed})
    return rows


def breaches(metrics: dict, thresholds: dict | None = None) -> list[str]:
    return [f"{r['metric']} is {r['value']}, {'below' if r['direction'] == 'min' else 'above'} {r['limit']}"
            for r in check(metrics, thresholds) if not r["passed"]]


def markdown(rows: list[dict], meta: dict) -> str:
    lines = [f"### Eval gate: {meta.get('provider')} {meta.get('model')}, {meta.get('subset')} subset",
             "", "| Metric | Value | Threshold | Gate | Result |", "| --- | --- | --- | --- | --- |"]
    for r in rows:
        gate = "clean run" if r["metric"] in CLEAN_RUN else TARGETS.get(r["metric"], (None, None, "regression"))[2]
        sign = ">=" if r["direction"] == "min" else "<="
        value = "n/a" if r["value"] is None else r["value"]
        result = r.get("note") or ("pass" if r["passed"] else "**FAIL**")
        lines.append(f"| {r['metric']} | {value} | {sign} {r['limit']} | {gate} | {result} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail the build if an eval threshold is breached.")
    parser.add_argument("results", type=Path)
    for metric, direction, default in GATES:
        parser.add_argument(option(metric, direction), type=float, dest=metric, default=default,
                            help=f"default {default}" if default is not None else "off unless set")
    args = parser.parse_args()

    try:
        results = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read {args.results}: {exc}", file=sys.stderr)
        return 2
    if not results.get("cases"):
        print("The results file has no cases, so there is nothing to gate.", file=sys.stderr)
        return 2

    thresholds = {metric: getattr(args, metric) for metric, _, _ in GATES}
    rows = check(results["metrics"], thresholds)
    table = markdown(rows, results["meta"])
    print(table)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write(table + "\n")

    failed = breaches(results["metrics"], thresholds)
    for line in failed:
        print(f"FAILED  {line}", file=sys.stderr)
    if failed:
        return 1
    print("Gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
