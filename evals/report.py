"""Appends the rows for a results file to the tables in EVALS.md.

A recorded version gets a row in four tables: the headline numbers, the adversarial tickets on
their own, the guardrail and checker counts, and every category side by side. The judge adds
its own row to the reply quality table when it runs, because it runs separately and costs money.
A nightly CI run adds one row to the nightly table instead.

Rows are only ever added. A version that already has a row is refused, because the history of
honest numbers is the whole point of the tables.
"""

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

EVALS_FILE = Path(__file__).resolve().parent.parent / "docs" / "EVALS.md"
TABLE_START = "## Version history"
ADVERSARIAL_START = "## Adversarial tickets"
CONTROLS_START = "## Guardrails and checks"
CATEGORY_START = "## By category"
QUALITY_START = "## Reply quality"
VALIDATION_START = "## Judge validation"
NIGHTLY_START = "## Nightly runs"
CATEGORIES = ("all", "straightforward", "edge_case", "adversarial")


def fmt(value, kind: str = "ratio") -> str:
    if value is None:
        return "n/a"
    if kind == "inr":
        return f"Rs {value:.2f}"
    if kind == "ms":
        return f"{value / 1000:.1f} s"
    return f"{value:.2f}"


def checker_cell(meta: dict, default: bool = False) -> str:
    """On or off, and the checker's name when it is not the agent's own model."""
    if not meta.get("verify", default):
        return "off"
    checker = meta.get("checker")
    if not checker or checker == f"{meta['provider']}:{meta['model']}":
        return "on"
    return f"on, {checker.replace(':', ' ', 1)}"


def build_row(version: str, change: str, results: dict) -> str:
    m, meta = results["metrics"], results["meta"]
    cells = [
        version,
        change,
        f"{meta['provider']} {meta['model']}",
        f"{m['cases']} {meta['subset']}",
        fmt(m["intent_accuracy"]),
        fmt(m["escalation_recall"]),
        fmt(m["escalation_precision"]),
        fmt(m["groundedness"]),
        fmt(m.get("action_correctness")),
        fmt(m.get("forbidden_tool_rate")),
        fmt(m["retrieval_recall"]),
        fmt(m["parse_failure_rate"]),
        fmt(m["cost_inr_per_ticket"], "inr"),
        fmt(m["latency_ms_p50"], "ms"),
        fmt(m["latency_ms_p95"], "ms"),
        date.today().isoformat(),
    ]
    return "| " + " | ".join(cells) + " |\n"


def build_adversarial_row(version: str, results: dict) -> str | None:
    numbers = results["metrics"].get("by_category", {}).get("adversarial")
    if not numbers:
        return None
    cells = [version, str(numbers["cases"]), fmt(numbers["forbidden_tool_rate"]), fmt(numbers["escalation_recall"]),
             fmt(numbers["decision_accuracy"]), str(numbers["actions_taken"]), date.today().isoformat()]
    return "| " + " | ".join(cells) + " |\n"


def build_controls_row(version: str, results: dict) -> str:
    m = results["metrics"]
    cells = [
        version,
        checker_cell(results["meta"]),
        str(m.get("guardrail_denials", 0)),
        str(m.get("approvals_requested", 0)),
        fmt(m.get("approval_recall")),
        str(m.get("human_denials", 0)),
        str(m.get("verify_retries", 0)),
        str(m.get("verify_escalations", 0)),
        str(m.get("unbacked_action_claims", 0)),
        date.today().isoformat(),
    ]
    return "| " + " | ".join(cells) + " |\n"


def build_category_rows(version: str, results: dict) -> str:
    m = results["metrics"]
    groups = {"all": m, **m.get("by_category", {})}
    lines = []
    for name in CATEGORIES:
        g = groups.get(name)
        if not g:
            continue
        cells = [version, name, str(g["cases"]), fmt(g["intent_accuracy"]), fmt(g["decision_accuracy"]),
                 fmt(g["escalation_recall"]), fmt(g.get("action_correctness")), fmt(g["groundedness"]),
                 fmt(g.get("forbidden_tool_rate")), fmt(g["deflection_rate"]), fmt(g.get("reply_quality")),
                 date.today().isoformat()]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def build_quality_row(version: str, results: dict) -> str:
    m, judge = results["metrics"], results.get("judge") or {}
    means = m.get("judge_means") or {}
    cells = [version, f"{judge.get('provider', '?')} {judge.get('model', '?')}", str(m.get("judged_replies", 0)),
             *(fmt(means.get(d)) for d in ("accuracy", "completeness", "tone", "restraint")),
             fmt(m.get("reply_quality")), fmt(judge.get("cost_inr"), "inr"), date.today().isoformat()]
    return "| " + " | ".join(cells) + " |\n"


def build_nightly_row(label: str, results: dict, gate: str) -> str:
    m, meta = results["metrics"], results["meta"]
    cells = [label, f"{meta['provider']} {meta['model']}", f"{m['cases']} {meta['subset']}", fmt(m["intent_accuracy"]),
             fmt(m["escalation_recall"]), fmt(m.get("action_correctness")), fmt(m["groundedness"]),
             fmt(m.get("forbidden_tool_rate")), fmt(m["deflection_rate"]), fmt(m.get("reply_quality")),
             fmt(m["cost_inr_per_ticket"], "inr"), fmt(m["latency_ms_p95"], "ms"), meta.get("git_commit") or "?", gate]
    return "| " + " | ".join(cells) + " |\n"


def append_row(path: Path, version: str, row: str, heading: str = TABLE_START) -> None:
    text = path.read_text(encoding="utf-8")
    if heading not in text:
        raise SystemExit(f"{path} has no {heading!r} heading, so the table cannot be found")
    before, after = text.split(heading, 1)
    lines = after.split("\n")

    start = next(i for i, line in enumerate(lines) if line.startswith("|"))
    end = start
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    if any(line.startswith(f"| {version} |") for line in lines[start:end]):
        raise SystemExit(f"{version} already has a row. Rows are never overwritten, use a new version name.")

    lines.insert(end, row.rstrip("\n"))
    path.write_text(before + heading + "\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Append a results row to docs/EVALS.md.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--version", help="for example v1")
    parser.add_argument("--change", help="what changed since the last version")
    parser.add_argument("--file", type=Path, default=EVALS_FILE)
    parser.add_argument("--nightly", action="store_true", help="add one row to the nightly table instead")
    parser.add_argument("--recompute", action="store_true",
                        help="score the cases again with the current metrics code, for files from an older phase")
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    if args.recompute:
        from evals.metrics import compute

        results["metrics"] = compute(results["cases"])
    if args.nightly:
        from evals.gate import breaches

        label = f"{date.today().isoformat()} {results['meta']['provider']}"
        gate = "fail" if breaches(results["metrics"]) else "pass"
        append_row(args.file, label, build_nightly_row(label, results, gate), NIGHTLY_START)
        print(f"Added a nightly row to {args.file}")
        return 0
    if not (args.version and args.change):
        parser.error("a recorded version needs --version and --change")
    if results["meta"]["subset"] != "full":
        print("Warning: this is not a full run. Recorded versions should use the full suite.", file=sys.stderr)
    if results["metrics"]["errors"]:
        print(f"Warning: {results['metrics']['errors']} cases crashed and count as wrong.", file=sys.stderr)

    rows = [(TABLE_START, build_row(args.version, args.change, results)),
            (ADVERSARIAL_START, build_adversarial_row(args.version, results)),
            (CONTROLS_START, build_controls_row(args.version, results)),
            (CATEGORY_START, build_category_rows(args.version, results))]
    if results["metrics"].get("judged_replies"):
        rows.append((QUALITY_START, build_quality_row(args.version, results)))
    rows = [(heading, row) for heading, row in rows if row]

    # Try every table on a copy first, so a refusal never leaves the file half written.
    with tempfile.TemporaryDirectory() as folder:
        scratch = Path(folder) / args.file.name
        scratch.write_text(args.file.read_text(encoding="utf-8"), encoding="utf-8")
        for heading, row in rows:
            append_row(scratch, args.version, row, heading)

    for heading, row in rows:
        append_row(args.file, args.version, row, heading)
    print(f"Added to {args.file}:\n" + "".join(row for _, row in rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
