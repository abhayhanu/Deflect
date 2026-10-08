"""Runs a reply checker over the drafts an earlier run recorded, without running the agent.

A full run on a local model takes hours, and almost none of that time is the checker. A results
file already holds every draft the checker read, so a different checker can read the same
drafts in minutes. For each round this shows what the recorded checker said, what the new one
says and where they differ, so the two can be compared before a full run is spent on it.

Only rounds where a model was asked are replayed. A round stopped by a rule never reached one.

The sources are rebuilt from the record: the policy sections retrieval returned, the order as
it was seeded and the actions that ran. Two things are approximate. Every round of a ticket
gets the final plan's citations and every action that ran, although an early round may have
come before a second plan or before the action.

A results file from v6 or earlier does not hold the orders, so they are read back from a
database seeded at the file's anchor with nothing acted on yet. The reseed option does that.
The checker is the one set in .env.

Given an earlier replay file, it also lists every draft whose verdict changed since then. That
is how a change to what the checker is shown gets tested: the drafts it was meant to fix
should move, and nothing else should.
"""

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from agent.guardrails.policy import CHECKER_UNSUPPORTED_AT
from agent.nodes.verify import model_claims, named, sentence_claims
from agent.providers import Decider, Usage, get_chat_model, get_checker
from agent.state import Plan, RetrievedPolicy, ToolCall
from data.index_policies import load_chunks
from evals.metrics import percentile
from evals.run import SECTION
from evals.sources import NotReady, attach_orders

log = logging.getLogger("evals.checker_replay")

MODEL_CHECKS = ([], ["unsupported_claims"])
THRESHOLDS = (0.3, 0.5, 0.7)


def chunks_by_section() -> dict[str, RetrievedPolicy]:
    found = {}
    for chunk in load_chunks():
        heading = SECTION.search(chunk.text)
        key = f"{chunk.doc_id}#{heading.group(1).strip() if heading else '?'}"
        found[key] = RetrievedPolicy(doc_id=chunk.doc_id, title=chunk.title, chunk=chunk.text, score=0.0)
    return found


def rounds_to_replay(results: dict, only: list[str] | None = None) -> list[tuple[dict, int, dict]]:
    """Every round where the recorded checker's model was asked, as record, position, round."""
    chosen = []
    for record in results["cases"]:
        if only and record["case_id"] not in only:
            continue
        for n, checked in enumerate(record.get("checker_rounds") or []):
            if checked["failed_checks"] in MODEL_CHECKS and checked.get("draft"):
                chosen.append((record, n, checked))
    return chosen


def rebuild(record: dict, draft: str, order: dict | None, chunks: dict, anchor: datetime) -> dict:
    """The state the checker reads, put back together from what the run recorded."""
    predicted = record["predicted"]
    plan = Plan(decision=predicted.get("plan_decision") or "answer", cites=predicted.get("cites") or [], rationale="")
    calls = [ToolCall(name=t["name"], args=t["args"], result=t["result"], error=t["error"], authorized_by=t["authorized_by"],
                      latency_ms=t["latency_ms"], called_at=anchor, approver_id=t.get("approver_id"))
             for t in record.get("tool_calls", [])]
    policies = [chunks[s] for s in predicted.get("retrieved_sections", []) if s in chunks]
    return {"plan": plan, "policies": policies, "order": order, "tool_calls": calls, "draft": draft}


def replay_round(checker, state: dict) -> dict:
    usage, started = Usage(), time.perf_counter()
    flagged, doubt, error = [], {}, None
    try:
        if isinstance(checker, Decider):
            flagged, doubt = sentence_claims(state, checker, usage)
        else:
            flagged = model_claims(state, checker, usage)
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    return {"flagged": flagged, "sentence_doubt": doubt, "error": error, "cost_inr": usage.cost_inr,
            "latency_ms": round((time.perf_counter() - started) * 1000)}


def flags_at(row: dict, threshold: float) -> bool:
    """Whether the replay sends the draft back at this threshold. A checker with no
    probabilities has only its own verdict."""
    if not row["sentence_doubt"]:
        return bool(row["flagged"])
    return any(p >= threshold for p in row["sentence_doubt"].values())


def tally(rows: list[dict], threshold: float) -> dict:
    ok = [r for r in rows if not r["error"]]
    both_pass = sum(not r["recorded_flagged"] and not flags_at(r, threshold) for r in ok)
    both_flag = sum(r["recorded_flagged"] and flags_at(r, threshold) for r in ok)
    return {
        "rounds": len(ok),
        "recorded_flagged": sum(r["recorded_flagged"] for r in ok),
        "replay_flagged": sum(flags_at(r, threshold) for r in ok),
        "both_pass": both_pass,
        "both_flag": both_flag,
        "only_recorded_flagged": sum(r["recorded_flagged"] and not flags_at(r, threshold) for r in ok),
        "only_replay_flagged": sum(not r["recorded_flagged"] and flags_at(r, threshold) for r in ok),
        "agreement": round((both_pass + both_flag) / len(ok), 3) if ok else None,
    }


def summarise(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"]]
    first = [r for r in ok if r["round"] == 0]
    latencies = [r["latency_ms"] for r in ok]
    has_doubt = any(r["sentence_doubt"] for r in ok)
    return {
        "errors": len(rows) - len(ok),
        "all_rounds": tally(rows, CHECKER_UNSUPPORTED_AT),
        # A ticket stops at the first draft that passes, so first drafts say the most about deflection.
        "first_drafts": tally(first, CHECKER_UNSUPPORTED_AT),
        "by_threshold": {str(t): tally(rows, t) for t in THRESHOLDS} if has_doubt else {},
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "cost_inr": round(sum(r["cost_inr"] for r in ok), 4),
    }


def show(title: str, numbers: dict) -> None:
    print(f"\n  {title}: {numbers['rounds']} drafts")
    print(f"    recorded checker sent back   {numbers['recorded_flagged']}")
    print(f"    this checker sends back      {numbers['replay_flagged']}")
    print(f"    both pass {numbers['both_pass']}, both send back {numbers['both_flag']}, "
          f"only recorded {numbers['only_recorded_flagged']}, only this one {numbers['only_replay_flagged']}")
    print(f"    agreement                    {numbers['agreement']}")


def show_differences(rows: list[dict], limit: int) -> None:
    eased = [r for r in rows if not r["error"] and r["recorded_flagged"] and not r["flagged"]]
    stricter = [r for r in rows if not r["error"] and not r["recorded_flagged"] and r["flagged"]]
    print(f"\n  Sent back by the recorded checker, passed by this one: {len(eased)}")
    for r in eased[:limit]:
        print(f"    {r['case_id']} round {r['round'] + 1}: {'; '.join(r['recorded_claims'])[:200]}")
    print(f"\n  Passed by the recorded checker, sent back by this one: {len(stricter)}")
    for r in stricter[:limit]:
        print(f"    {r['case_id']} round {r['round'] + 1}: {'; '.join(r['flagged'])[:200]}")


def show_changes(rows: list[dict], earlier: dict) -> None:
    before = {(r["case_id"], r["round"]): bool(r["flagged"]) for r in earlier["rounds"] if not r["error"]}
    both = [r for r in rows if not r["error"] and (r["case_id"], r["round"]) in before]
    eased = [r for r in both if before[(r["case_id"], r["round"])] and not r["flagged"]]
    stricter = [r for r in both if not before[(r["case_id"], r["round"])] and r["flagged"]]
    print(f"\n  Against the earlier replay by {earlier['checker']}: {len(both)} drafts in both, "
          f"{len(eased)} now pass, {len(stricter)} are now sent back")
    for r in eased:
        print(f"    now passes     {r['case_id']} round {r['round'] + 1}")
    for r in stricter:
        print(f"    now sent back  {r['case_id']} round {r['round'] + 1}: {'; '.join(r['flagged'])[:200]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay a reply checker over the drafts recorded in a results file.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--case", action="append", help="replay only this case id, can be repeated")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reseed", action="store_true", help="reset the shop data to the anchor of the results file first")
    parser.add_argument("--show", type=int, default=10, help="how many differences of each kind to print")
    parser.add_argument("--out", type=Path, default=Path("checker_replay.json"))
    parser.add_argument("--against", type=Path, help="an earlier replay file, to list the drafts whose verdict changed")
    parser.add_argument("--checker", help="overrides DEFLECT_CHECKER_PROVIDER for this replay. agent means the agent's own model")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    results = json.loads(args.results.read_text(encoding="utf-8"))
    anchor = datetime.fromisoformat(results["meta"]["seed_anchor"])
    earlier = json.loads(args.against.read_text(encoding="utf-8")) if args.against else None
    if args.checker:
        os.environ["DEFLECT_CHECKER_PROVIDER"] = "" if args.checker == "agent" else args.checker
        os.environ["DEFLECT_CHECKER_MODEL"] = ""
    try:
        checker = get_checker(get_chat_model("agent"))
    except (ImportError, ValueError) as exc:
        print(f"Cannot start the reply checker: {exc}", file=sys.stderr)
        return 2

    chosen = rounds_to_replay(results, args.case)
    if not chosen:
        print("This results file has no checker rounds with a draft to replay. It needs a run from v4 or later.", file=sys.stderr)
        return 2
    try:
        attach_orders(results, args.reseed)
    except NotReady as exc:
        print(exc, file=sys.stderr)
        return 2
    chunks = chunks_by_section()

    was = results["meta"].get("checker") or f"{results['meta']['provider']}:{results['meta']['model']}"
    print(f"Replaying {len(chosen)} drafts from {args.results.name}, recorded checker {was}, this checker {named(checker)}")

    def one(item) -> dict:
        record, n, checked = item
        state = rebuild(record, checked["draft"], record["order"], chunks, anchor)
        row = replay_round(checker, state)
        return {"case_id": record["case_id"], "round": n, "recorded_flagged": bool(checked["failed_checks"]),
                "recorded_claims": checked["unsupported_claims"], "draft": checked["draft"], **row}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(one, chosen))

    summary = summarise(rows)
    args.out.write_text(json.dumps({
        "results": args.results.name, "recorded_checker": was, "checker": named(checker),
        "threshold": CHECKER_UNSUPPORTED_AT, "summary": summary, "rounds": rows}, indent=2), encoding="utf-8")

    if summary["errors"]:
        first = next(r["error"] for r in rows if r["error"])
        print(f"\n  {summary['errors']} drafts could not be checked, the first error was: {first}", file=sys.stderr)
    show("All drafts", summary["all_rounds"])
    show("First drafts only", summary["first_drafts"])
    for threshold, numbers in summary["by_threshold"].items():
        print(f"\n  At threshold {threshold}: sends back {numbers['replay_flagged']} of {numbers['rounds']}, "
              f"agreement {numbers['agreement']}")
    print(f"\n  checker latency p50 {summary['latency_ms_p50']} ms, p95 {summary['latency_ms_p95']} ms, "
          f"cost Rs {summary['cost_inr']:.2f} for the whole replay")
    show_differences(rows, args.show)
    if earlier:
        show_changes(rows, earlier)
    print(f"\nWrote {args.out}. Agreement is not accuracy: read the differences, the recorded checker was often the one that was wrong.")
    return 1 if summary["errors"] == len(rows) else 0


if __name__ == "__main__":
    sys.exit(main())
