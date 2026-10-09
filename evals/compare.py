"""Puts several full runs side by side, one row per model configuration.

Give it one results file per configuration, for example a small local model, a larger local
model and a hosted one. With the write option the table replaces the model comparison block in
EVALS.md. Unlike the version history this table is a snapshot, rebuilt from the files each
time, so its source files are listed under it.

Two runs of the same models on different code look the same in the table. Write a file as
a label, an equals sign and the path, and the label is added to its row.
"""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from evals.report import EVALS_FILE, checker_cell, fmt, model_cell

START = "<!-- model comparison start -->"
END = "<!-- model comparison end -->"
HEADER = ("| Configuration | Cases | Checker | Intent acc | Decision acc | Esc. recall | Esc. precision | Grounded "
          "| Action correct | Forbidden tool rate | Deflection | Reply quality | Parse fail rate | Cost per ticket "
          "| Latency p50 | Latency p95 |")


def labelled(argument: str) -> tuple[str | None, Path]:
    """Splits a label from its file. A path that exists as written has no label."""
    if "=" in argument and not Path(argument).exists():
        label, path = argument.split("=", 1)
        return label.strip() or None, Path(path)
    return None, Path(argument)


def row(results: dict, label: str | None = None) -> str:
    m, meta = results["metrics"], results["meta"]
    cells = [model_cell(meta) + (f", {label}" if label else ""), f"{m['cases']} {meta['subset']}",
             checker_cell(meta, default=True), fmt(m["intent_accuracy"]), fmt(m["decision_accuracy"]),
             fmt(m["escalation_recall"]), fmt(m.get("escalation_precision")), fmt(m.get("groundedness")),
             fmt(m.get("action_correctness")), fmt(m.get("forbidden_tool_rate")),
             fmt(m["deflection_rate"]), fmt(m.get("reply_quality")), fmt(m["parse_failure_rate"]),
             fmt(m["cost_inr_per_ticket"], "inr"), fmt(m["latency_ms_p50"], "ms"), fmt(m["latency_ms_p95"], "ms")]
    return "| " + " | ".join(cells) + " |"


def table(arguments: list) -> str:
    named = [labelled(str(a)) for a in arguments]
    files = [path for _, path in named]
    runs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    subsets = {(r["meta"]["subset"], r["meta"].get("verify", True)) for r in runs}
    if len(subsets) > 1:
        print("Warning: these runs differ in subset or checker, so the rows are not a fair comparison.", file=sys.stderr)
    lines = [HEADER, "| " + " | ".join(["---"] * (HEADER.count("|") - 1)) + " |"]
    lines += [row(r, label) for r, (label, _) in zip(runs, named)]
    sources = ", ".join(f"`{f.name}`" for f in files)
    lines += ["", f"Built on {date.today().isoformat()} from {sources}."]
    return "\n".join(lines)


def write(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{path} has no model comparison markers, so the table has nowhere to go")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    path.write_text(f"{before}{START}\n{block}\n{END}{after}", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare full eval runs across model configurations.")
    parser.add_argument("results", nargs="+", help="a results file, or a label, an equals sign and a results file")
    parser.add_argument("--write", action="store_true", help="replace the model comparison table in EVALS.md")
    parser.add_argument("--file", type=Path, default=EVALS_FILE)
    args = parser.parse_args()

    block = table(args.results)
    print(block)
    if args.write:
        write(args.file, block)
        print(f"\nWrote the comparison into {args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
