"""Checks the judge against a person: you grade 20 replies, then this measures how often you agree.

Two steps. The sheet step picks 20 replies the judge can score and writes them out to read,
with an empty CSV for your grades. It shows no judge scores, so your grades stay blind. The
score step reads your CSV and the judge's scores for the same replies and reports exact
agreement and agreement within one point, per dimension and overall, and lists the replies
where the two are furthest apart with the judge's note on each.

The sheet shows everything the judge is shown, the order record included. A grader who cannot
see the order has to take the reply's word for it, and a judge who cannot see it has to assume
the worst. Both happened in the first round. A later round gets its own files, so earlier
grades are never written over.

If exact agreement is under 70 percent the rubric is too vague: tighten its anchor
descriptions, judge again and grade again.
"""

import argparse
import csv
import json
import random
import sys
from datetime import date
from pathlib import Path

from agent.guardrails.redact import redact
from evals.judge import DIMENSIONS, RUBRIC, action_text, golden_messages, order_text, should_judge
from evals.report import EVALS_FILE, VALIDATION_START, append_row, judge_name

FOLDER = Path(__file__).resolve().parent / "judge_validation"
SAMPLE_SIZE = 20
AGREEMENT_BAR = 0.70


def pick(records: list[dict], n: int, seed: int = 7) -> list[dict]:
    """A fixed random sample, split between answered and acted tickets in their real proportion."""
    pool = [r for r in records if should_judge(r) is None]
    rng = random.Random(seed)
    groups = {}
    for r in pool:
        groups.setdefault(r["predicted"]["terminal_reason"], []).append(r)
    chosen = []
    for name, group in sorted(groups.items()):
        share = round(n * len(group) / len(pool))
        chosen += rng.sample(group, min(share, len(group)))
    rest = [r for r in pool if r not in chosen]
    chosen += rng.sample(rest, max(0, min(n - len(chosen), len(rest))))
    return sorted(chosen[:n], key=lambda r: r["case_id"])


def sheet(records: list[dict], source: str) -> str:
    messages = golden_messages()
    parts = [f"# Judge validation sheet\n\nFrom `{source}`. Grade each reply on the four dimensions of the rubric "
             "below, 1 to 5, and write the numbers into `human_grades.csv`. The policies are in `data/policies`. "
             f"Do not look at the judge's scores until you are done.\n\n```\n{RUBRIC}\n```\n"]
    for n, r in enumerate(records, start=1):
        parts.append(f"## {n}. {r['case_id']}\n\n**Customer**\n\n> {redact(messages[r['case_id']])[0]}\n\n"
                     f"**Policies available:** {', '.join(r['predicted']['retrieved_ids']) or 'none'}\n\n"
                     f"**Order record**\n\n```\n{order_text(r)}\n```\n\n"
                     f"**Action taken**\n\n```\n{action_text(r)}\n```\n\n**Reply**\n\n> "
                     + redact(r["reply"])[0].replace("\n", "\n> ") + "\n")
    return "\n".join(parts)


def read_grades(path: Path) -> dict[str, dict]:
    grades = {}
    with path.open(encoding="utf-8", newline="") as f:
        for line in csv.DictReader(f):
            values = {d: line.get(d, "").strip() for d in DIMENSIONS}
            if all(values.values()):
                grades[line["case_id"]] = {d: int(v) for d, v in values.items()}
    return grades


def agreement(human: dict[str, dict], judge: dict[str, dict]) -> dict:
    shared = sorted(set(human) & set(judge))
    out = {"replies": len(shared), "by_dimension": {}}
    pairs_all = []
    for d in DIMENSIONS:
        pairs = [(human[c][d], judge[c][d]) for c in shared]
        pairs_all += pairs
        out["by_dimension"][d] = {
            "exact": round(sum(h == j for h, j in pairs) / len(pairs), 3) if pairs else None,
            "within_one": round(sum(abs(h - j) <= 1 for h, j in pairs) / len(pairs), 3) if pairs else None,
            "judge_minus_human": round(sum(j - h for h, j in pairs) / len(pairs), 2) if pairs else None,
        }
    out["exact"] = round(sum(h == j for h, j in pairs_all) / len(pairs_all), 3) if pairs_all else None
    out["within_one"] = round(sum(abs(h - j) <= 1 for h, j in pairs_all) / len(pairs_all), 3) if pairs_all else None
    return out


def furthest_apart(human: dict[str, dict], judge: dict[str, dict], gap: int = 2) -> list[str]:
    """One line per reply where some dimension differs by the gap or more, widest first."""
    lines = []
    for case in sorted(set(human) & set(judge)):
        apart = {d: judge[case][d] - human[case][d] for d in DIMENSIONS if abs(judge[case][d] - human[case][d]) >= gap}
        if apart:
            said = ", ".join(f"{d} you {human[case][d]} judge {judge[case][d]}" for d in apart)
            lines.append((max(abs(v) for v in apart.values()), f"{case}: {said}. Judge: {judge[case].get('notes', '')[:160]}"))
    return [text for _, text in sorted(lines, key=lambda x: -x[0])]


def file_names(round_number: int | None) -> tuple[str, str]:
    tail = f"_{round_number}" if round_number else ""
    return f"grading_sheet{tail}.md", f"human_grades{tail}.csv"


def validation_row(result: dict, judge_label: str, source: str) -> str:
    cells = [date.today().isoformat(), judge_label, source, str(result["replies"]), f"{result['exact']:.2f}",
             f"{result['within_one']:.2f}"]
    cells += [f"{result['by_dimension'][d]['exact']:.2f}" for d in DIMENSIONS]
    cells.append("yes" if result["exact"] >= AGREEMENT_BAR else "no, tighten the rubric")
    return "| " + " | ".join(cells) + " |\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the judge against your own grades.")
    steps = parser.add_subparsers(dest="step", required=True)
    make = steps.add_parser("sheet", help="pick replies and write the grading sheet")
    make.add_argument("results", type=Path)
    make.add_argument("--n", type=int, default=SAMPLE_SIZE)
    make.add_argument("--out", type=Path, default=FOLDER)
    make.add_argument("--round", type=int, help="a later round of grading, written to its own files, for example 2")
    make.add_argument("--reseed", action="store_true",
                      help="reset the shop data to the anchor of the results file, to read back orders an older file lacks")
    score = steps.add_parser("score", help="compare your grades with the judge")
    score.add_argument("results", type=Path)
    score.add_argument("--grades", type=Path, default=FOLDER / "human_grades.csv")
    score.add_argument("--record", action="store_true", help="append the result to EVALS.md")
    args = parser.parse_args()

    results = json.loads(args.results.read_text(encoding="utf-8"))
    if args.step == "sheet":
        from evals.sources import NotReady, attach_orders

        sheet_name, grades_name = file_names(args.round)
        if (args.out / grades_name).exists() and read_grades(args.out / grades_name):
            print(f"{args.out / grades_name} already holds grades and is never written over. "
                  "Start a new round with --round, for example --round 2.", file=sys.stderr)
            return 2
        try:
            if attach_orders(results, args.reseed):
                args.results.write_text(json.dumps(results, indent=2), encoding="utf-8")
        except NotReady as exc:
            print(exc, file=sys.stderr)
            return 2
        chosen = pick(results["cases"], args.n)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / sheet_name).write_text(sheet(chosen, args.results.name), encoding="utf-8")
        with (args.out / grades_name).open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["case_id", *DIMENSIONS, "notes"])
            writer.writerows([[r["case_id"], "", "", "", "", ""] for r in chosen])
        print(f"Wrote {len(chosen)} replies to {args.out / sheet_name} and an empty {args.out / grades_name}")
        print("Judge the same replies with: python -m evals.judge " + str(args.results) + " "
              + " ".join(f"--case {r['case_id']}" for r in chosen))
        return 0

    human = read_grades(args.grades)
    judge = {r["case_id"]: r["judge"] for r in results["cases"] if "accuracy" in (r.get("judge") or {})}
    missing = sorted(set(human) - set(judge))
    if not human:
        print(f"No complete rows in {args.grades} yet.", file=sys.stderr)
        return 2
    if missing:
        print(f"The judge has not scored {', '.join(missing)}. Run evals.judge on {args.results} first.", file=sys.stderr)
        return 2

    result = agreement(human, judge)
    print(f"Replies compared     {result['replies']}")
    print(f"Exact agreement      {result['exact']:.0%}")
    print(f"Within one point     {result['within_one']:.0%}")
    for d in DIMENSIONS:
        x = result["by_dimension"][d]
        print(f"  {d:<14} exact {x['exact']:.0%}  within one {x['within_one']:.0%}  judge minus you {x['judge_minus_human']:+.2f}")
    verdict = "passes" if result["exact"] >= AGREEMENT_BAR else "is under"
    print(f"\nExact agreement {verdict} the {AGREEMENT_BAR:.0%} bar.")
    apart = furthest_apart(human, judge)
    if apart:
        print(f"\nFurthest apart, {len(apart)} replies. For each one decide who was right before changing anything:")
        for line in apart:
            print(f"  {line}")
    if args.record:
        label = judge_name(results.get("judge") or {})
        source = args.results.name if args.grades.name == "human_grades.csv" else f"{args.results.name}, {args.grades.name}"
        row = validation_row(result, label, source)
        append_row(EVALS_FILE, f"{date.today().isoformat()} | {label} | {source}", row, VALIDATION_START)
        print(f"Added the result to {EVALS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
