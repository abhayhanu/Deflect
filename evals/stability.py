"""Shows how steady escalation is from one run to the next.

A hosted model does not make the same plan twice. One run that passes the gate can be the
same agent as one run that fails it, with a single ticket landing the other way. Thirty
tickets must go to a person, so each one is worth three points of recall, and the gate at
0.95 has room for exactly one miss.

Run the same tickets several times on the same code, give this every results file, and it
shows for each ticket that must go to a person how many of the runs sent it there, and what
decided it. The escalation subset of the eval runner is those thirty tickets and nothing
else. Most of them stop early, so a pass costs about a tenth of a full run.

A ticket stopped by a rule in code is stopped the same way every time. A ticket stopped by
a model's judgement is stopped most of the time. The second kind is where recall is lost.
"""

import argparse
import json
import sys
from pathlib import Path

# Reasons a ticket reached a person that no model was asked about.
BY_CODE = ("escalated_message_signal", "escalated_guardrail_", "escalated_human_denied", "escalated_tool_error")


def decided_by_code(reason: str | None) -> bool:
    return (reason or "").startswith(BY_CODE)


def ended(record: dict) -> str:
    return record["predicted"].get("terminal_reason") or ("error" if record.get("error") else "stopped")


def compare(runs: list[dict]) -> dict:
    """runs are whole results files. Only tickets present in every one of them are compared."""
    by_run = [{r["case_id"]: r for r in run["cases"]} for run in runs]
    shared = [case_id for case_id in by_run[0] if all(case_id in cases for cases in by_run)]
    must = [case_id for case_id in shared if by_run[0][case_id]["expected"]["must_escalate"]]

    tickets = []
    for case_id in must:
        reasons = [ended(cases[case_id]) for cases in by_run]
        sent = sum(cases[case_id]["predicted"]["escalated"] and not cases[case_id].get("error") for cases in by_run)
        tickets.append({"case_id": case_id, "escalated": sent, "runs": len(runs), "ended": reasons,
                        "by_code": all(decided_by_code(reason) for reason in reasons)})
    recalls = [round(sum(cases[c]["predicted"]["escalated"] and not cases[c].get("error") for c in must) / len(must), 4)
               for cases in by_run] if must else []
    changed = sorted(c for c in shared if len({by_run[n][c]["predicted"]["decision"] for n in range(len(runs))}) > 1)
    return {
        "runs": len(runs),
        "tickets": len(shared),
        "must_escalate": len(must),
        "recall_per_run": recalls,
        "lowest_recall": min(recalls) if recalls else None,
        "always": sorted(t["case_id"] for t in tickets if t["escalated"] == len(runs)),
        "sometimes": [t for t in tickets if 0 < t["escalated"] < len(runs)],
        "never": [t for t in tickets if t["escalated"] == 0],
        "held_by_code": sorted(t["case_id"] for t in tickets if t["by_code"]),
        "decisions_that_changed": changed,
    }


def show(s: dict, names: list[str]) -> None:
    print(f"{s['runs']} runs, {s['tickets']} tickets in all of them, {s['must_escalate']} that must go to a person")
    print("  escalation recall per run  " + "  ".join(f"{name} {value:.2f}" for name, value in zip(names, s["recall_per_run"])))
    print(f"  lowest {s['lowest_recall']:.2f}. That is the number to hold against the gate, not the best one.")
    print(f"\n  sent to a person in every run   {len(s['always'])}")
    print(f"  of those, by a rule in code     {len(s['held_by_code'])}   the rest by a model's judgement each time")
    for title, group in (("in some runs", s["sometimes"]), ("in no run", s["never"])):
        print(f"\n  sent to a person {title}   {len(group)}")
        for t in group:
            print(f"    {t['case_id']}  {t['escalated']} of {t['runs']}   {', '.join(t['ended'])}")
    print(f"\n  tickets whose final decision differed between runs   {len(s['decisions_that_changed'])}")
    if s["decisions_that_changed"]:
        print("    " + " ".join(s["decisions_that_changed"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare the same tickets across several runs of the same code.")
    parser.add_argument("results", type=Path, nargs="+", help="two or more results files")
    parser.add_argument("--out", type=Path, help="also write the comparison as JSON")
    args = parser.parse_args()
    if len(args.results) < 2:
        parser.error("give at least two results files, one run says nothing about steadiness")

    runs = [json.loads(path.read_text(encoding="utf-8")) for path in args.results]
    setups = {(r["meta"]["provider"], r["meta"]["model"], r["meta"].get("classifier"), r["meta"].get("checker")) for r in runs}
    if len(setups) > 1:
        print("Warning: these runs do not share a model setup, so a difference may be the setup and not chance.", file=sys.stderr)
    commits = {r["meta"].get("git_commit") for r in runs}
    if len(commits) > 1:
        print("Warning: these runs were made on different commits, so a difference may be the code and not chance.", file=sys.stderr)

    summary = compare(runs)
    show(summary, [path.stem for path in args.results])
    if args.out:
        args.out.write_text(json.dumps({"files": [p.name for p in args.results], **summary}, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
