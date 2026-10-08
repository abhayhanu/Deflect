"""Scores each step of the graph on its own, from a run that is already recorded.

The headline metrics say how a ticket ended. They cannot say which step lost it. This reads
one results file and gives every step its own numbers. No model is called.

    classifier   intent against the label, per intent
    retriever    whether the required policies came back, how high they ranked, and per policy
    planner      the decision against the label, split by whether its inputs were right
    guardrail    what it refused, and whether the label wanted an action on that ticket
    generator    how often a first draft passed, and what was wrong with the others
    first fault  for every ticket the agent should have closed and did not, the first step
                 that went wrong

A later step only sees what the steps before it produced, so these are its numbers on real
inputs, mistakes included. The planner's split by input quality is the nearest a recorded run
gets to isolating it. evals.isolate goes the rest of the way and runs one step with the steps
before it replaced by the answer key.

The first fault is the earliest step that was wrong on a ticket. It is where to look first.
It is not proof of the cause, because a later step might have failed anyway.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from agent.guardrails.policy import CHECKER_UNSUPPORTED_AT
from evals.metrics import DIMENSIONS, judged, ratio, same_value
from evals.report import EVALS_FILE, checker_cell, model_cell

START = "<!-- component level start -->"
END = "<!-- component level end -->"
# Checks a draft can fail because of how it was written. The others are the plan's doing.
WRITING_CHECKS = ("unsupported_claims", "claims_unperformed_action", "wrong_refund_timeline", "template_slot")
STOPPED_EARLY = ("escalated_low_confidence", "escalated_out_of_scope", "escalated_parse_failure")
FAULTS = ("classifier", "retriever", "planner", "guardrail", "generator or checker", "other")


def docs(record: dict) -> list[str]:
    """The policy of each section the plan was shown: the search's hits in rank order, then
    any pinned sections the search had not ranked."""
    return [section.split("#")[0] for section in record["predicted"].get("retrieved_sections") or []]


def searched(record: dict) -> list[str]:
    """Only what the search itself ranked. Results from before v9 have no pinned sections."""
    pinned = set(record["predicted"].get("pinned_sections") or [])
    return [section.split("#")[0] for section in record["predicted"].get("retrieved_sections") or [] if section not in pinned]


def to_find(record: dict) -> list[str]:
    """The required policies the search is answerable for. One that is pinned arrives anyway."""
    given = {section.split("#")[0] for section in record["predicted"].get("pinned_sections") or []}
    return [doc for doc in required(record) if doc not in given]


def required(record: dict) -> list[str]:
    return record["expected"]["required_policy_ids"]


def all_retrieved(record: dict) -> bool:
    return set(required(record)) <= set(docs(record))


def intent_right(record: dict) -> bool:
    return record["predicted"]["intent"] == record["expected"]["intent"]


def first_plan(record: dict) -> dict | None:
    """What the planner chose before any reply was sent back to it. Older results files kept
    only the last plan, so for them that one is used."""
    rounds = record.get("plan_rounds") or []
    if rounds:
        return rounds[0]
    decision = record["predicted"].get("plan_decision")
    return {"decision": decision} if decision else None


def classifier(records: list[dict]) -> dict:
    intents = sorted({r["expected"]["intent"] for r in records})
    per_intent = {}
    for intent in intents:
        labelled = [r for r in records if r["expected"]["intent"] == intent]
        said = [r for r in records if r["predicted"]["intent"] == intent]
        right = sum(intent_right(r) for r in labelled)
        per_intent[intent] = {"labelled": len(labelled), "right": right, "recall": ratio(right, len(labelled)),
                              "precision": ratio(sum(intent_right(r) for r in said), len(said))}
    wrong = Counter((r["expected"]["intent"], r["predicted"]["intent"]) for r in records if not intent_right(r))
    return {
        "tickets": len(records),
        "right": sum(intent_right(r) for r in records),
        "accuracy": ratio(sum(intent_right(r) for r in records), len(records)),
        "per_intent": per_intent,
        "confusions": [[f"{a} read as {b}", n] for (a, b), n in wrong.most_common()],
        "stand_ins": sum(bool(r["predicted"].get("classifier_fell_back")) for r in records),
    }


def reciprocal_rank(record: dict) -> float:
    for rank, doc in enumerate(searched(record), start=1):
        if doc in to_find(record):
            return 1 / rank
    return 0.0


def retriever(records: list[dict]) -> dict:
    """Scored on the tickets that reached the search and whose label names a policy."""
    reached = [r for r in records if docs(r) and required(r)]
    by_policy: dict[str, list[int]] = {}
    for r in reached:
        for doc in required(r):
            found = by_policy.setdefault(doc, [0, 0])
            found[0] += doc in docs(r)
            found[1] += 1
    right_intent = [r for r in reached if intent_right(r)]
    wrong_intent = [r for r in reached if not intent_right(r)]
    # Ranking is scored on the search alone, and only where it had something to find.
    ranked = [r for r in reached if to_find(r) and searched(r)]
    return {
        "tickets": len(reached),
        "complete": sum(all_retrieved(r) for r in reached),
        # Every required policy is somewhere in what the plan was shown.
        "recall": ratio(sum(all_retrieved(r) for r in reached), len(reached)),
        "ranked_tickets": len(ranked),
        "sections_searched": round(sum(len(searched(r)) for r in ranked) / len(ranked), 1) if ranked else None,
        # The share of the search's sections that belong to a required policy.
        "precision": round(sum(sum(d in to_find(r) for d in searched(r)) / len(searched(r)) for r in ranked) / len(ranked), 4)
        if ranked else None,
        # One over the rank of the first section from a required policy, averaged.
        "mrr": round(sum(reciprocal_rank(r) for r in ranked) / len(ranked), 4) if ranked else None,
        "only_by_the_pin": sum(all_retrieved(r) and not set(to_find(r)) >= set(required(r)) for r in reached),
        "per_policy": {doc: {"needed": n, "found": hit, "recall": ratio(hit, n)} for doc, (hit, n) in sorted(by_policy.items())},
        "recall_intent_right": ratio(sum(all_retrieved(r) for r in right_intent), len(right_intent)),
        "recall_intent_wrong": ratio(sum(all_retrieved(r) for r in wrong_intent), len(wrong_intent)),
        "misses": [{"case_id": r["case_id"], "missing": sorted(set(required(r)) - set(docs(r))), "intent_right": intent_right(r)}
                   for r in reached if not all_retrieved(r)],
    }


def plan_right(record: dict) -> bool:
    return first_plan(record)["decision"] == record["expected"]["decision"]


def planner(records: list[dict]) -> dict:
    planned = [r for r in records if first_plan(r)]
    good = [r for r in planned if intent_right(r) and all_retrieved(r)]
    bad = [r for r in planned if not (intent_right(r) and all_retrieved(r))]
    by_label = {}
    for decision in ("answer", "act", "escalate"):
        labelled = [r for r in planned if r["expected"]["decision"] == decision]
        by_label[decision] = {"labelled": len(labelled), "right": sum(plan_right(r) for r in labelled),
                              "recall": ratio(sum(plan_right(r) for r in labelled), len(labelled))}
    chose = Counter((r["expected"]["decision"], first_plan(r)["decision"]) for r in planned)
    acts = [r for r in planned if r["expected"]["decision"] == "act" and first_plan(r)["decision"] == "act"
            and "tool_name" in first_plan(r)]
    return {
        "tickets": len(planned),
        "right": sum(plan_right(r) for r in planned),
        "accuracy": ratio(sum(plan_right(r) for r in planned), len(planned)),
        "inputs_right": {"tickets": len(good), "right": sum(plan_right(r) for r in good),
                         "accuracy": ratio(sum(plan_right(r) for r in good), len(good))},
        "an_input_wrong": {"tickets": len(bad), "right": sum(plan_right(r) for r in bad),
                           "accuracy": ratio(sum(plan_right(r) for r in bad), len(bad))},
        "by_label": by_label,
        "chose": [[f"{label} planned as {made}", n] for (label, made), n in sorted(chose.items())],
        # The two kinds of wrong decision are not equally bad.
        "sent_to_a_person_needlessly": sum(n for (label, made), n in chose.items() if label != "escalate" and made == "escalate"),
        "handled_what_needed_a_person": sum(n for (label, made), n in chose.items() if label == "escalate" and made != "escalate"),
        "right_tool": ratio(sum(first_plan(r)["tool_name"] == r["expected"]["tool_name"] for r in acts), len(acts)),
        "right_arguments": ratio(sum(first_plan(r)["tool_name"] == r["expected"]["tool_name"] and all(
            same_value(v, (first_plan(r).get("tool_args") or {}).get(k)) for k, v in (r["expected"]["tool_args"] or {}).items())
            for r in acts), len(acts)),
        "first_plan_recorded": any(r.get("plan_rounds") for r in records),
    }


def guardrail(records: list[dict]) -> dict:
    ran = [r for r in records if r.get("guardrail")]
    denied = [r for r in ran if r["guardrail"]["outcome"] == "deny"]
    return {
        "tickets": len(ran),
        "allowed": sum(r["guardrail"]["outcome"] == "allow" for r in ran),
        "sent_for_approval": sum((r.get("approval") or {}).get("requested", False) for r in ran),
        "denied": len(denied),
        "denied_by_check": dict(Counter(r["guardrail"]["check"] for r in denied)),
        # A denial here is correct by the label. The plan chose an action where none was wanted.
        "denied_no_action_wanted": sorted(r["case_id"] for r in denied if r["expected"]["decision"] != "act"),
        # A denial here needs reading. Either the plan chose the wrong action, or the rule is too strict.
        "denied_action_wanted": sorted(r["case_id"] for r in denied if r["expected"]["decision"] == "act"),
    }


def generator(records: list[dict]) -> dict:
    """Scored on the first draft of every ticket that reached the writer. Later drafts were
    written with the checker's complaint in the prompt, so they are not the writer alone."""
    drafted = [r for r in records if r.get("checker_rounds")]
    first = [r["checker_rounds"][0] for r in drafted]
    sent = [r for r in drafted if r["predicted"].get("terminal_reason") in ("answered", "acted")]
    doubts = [p for check in first for p in (check.get("sentence_doubt") or {}).values()]
    scores = judged(records)
    complete = [r for r in drafted if required(r) and all_retrieved(r)]
    incomplete = [r for r in drafted if required(r) and not all_retrieved(r)]

    def passed(group: list[dict]) -> int:
        return sum(r["checker_rounds"][0]["verdict"] == "pass" for r in group)

    return {
        "tickets": len(drafted),
        "first_draft_passed": passed(drafted),
        "first_draft_pass_rate": ratio(passed(drafted), len(drafted)),
        "sent": len(sent),
        "drafts_per_ticket": round(sum(len(r["checker_rounds"]) for r in drafted) / len(drafted), 2) if drafted else None,
        "first_draft_faults": dict(Counter(c for check in first for c in check["failed_checks"] if c in WRITING_CHECKS)),
        "stopped_by_the_plan": sum(any(c not in WRITING_CHECKS for c in check["failed_checks"]) for check in first),
        # Faithfulness, sentence by sentence, where the checker gives a probability.
        "sentences_checked": len(doubts),
        "sentences_supported": sum(p < CHECKER_UNSUPPORTED_AT for p in doubts),
        "sentence_support": ratio(sum(p < CHECKER_UNSUPPORTED_AT for p in doubts), len(doubts)),
        "judged_replies": len(scores),
        "judge_means": {d: round(sum(s[d] for s in scores) / len(scores), 2) for d in DIMENSIONS} if scores else {},
        # The retriever and the writer as a pipeline: does a missing policy show in the first draft?
        "pass_rate_policies_complete": ratio(passed(complete), len(complete)),
        "pass_rate_a_policy_missing": ratio(passed(incomplete), len(incomplete)),
        "tickets_a_policy_missing": len(incomplete),
        "stand_ins": sum(bool(c.get("checker_fell_back")) for r in drafted for c in r["checker_rounds"]),
    }


def first_fault(record: dict) -> str | None:
    """The earliest step that went wrong on a ticket the agent should have closed itself.
    None when the label says escalate, or when the ticket ended the way the label says."""
    expected, predicted = record["expected"], record["predicted"]
    if expected["decision"] == "escalate" or predicted["decision"] == expected["decision"]:
        return None
    if predicted.get("terminal_reason") == "escalated_message_signal":
        # A rule read the message and stopped the ticket, whatever the classifier said.
        return "guardrail"
    if not intent_right(record) or predicted.get("terminal_reason") in STOPPED_EARLY:
        return "classifier"
    if required(record) and not all_retrieved(record):
        return "retriever"
    plan = first_plan(record)
    if plan is None or plan["decision"] != expected["decision"]:
        return "planner"
    if (record.get("guardrail") or {}).get("outcome") == "deny" or (record.get("approval") or {}).get("decision") == "denied":
        return "guardrail"
    if predicted.get("terminal_reason") in ("escalated_verify_failed", "escalated_action_mismatch"):
        return "generator or checker"
    return "other"


def faults(records: list[dict]) -> dict:
    should_close = [r for r in records if r["expected"]["decision"] != "escalate"]
    found = Counter(fault for r in should_close if (fault := first_fault(r)))
    return {
        "should_close": len(should_close),
        "closed_right": sum(first_fault(r) is None for r in should_close),
        "lost": sum(found.values()),
        "first_fault": {name: found.get(name, 0) for name in FAULTS},
        "tickets": {name: sorted(r["case_id"] for r in should_close if first_fault(r) == name) for name in FAULTS},
    }


def compute(records: list[dict]) -> dict:
    ok = [r for r in records if not r.get("error")]
    return {"tickets": len(records), "crashed": len(records) - len(ok), "classifier": classifier(ok), "retriever": retriever(ok),
            "planner": planner(ok), "guardrail": guardrail(ok), "generator": generator(ok), "faults": faults(ok)}


def weakest(table: dict, count: str) -> tuple[str, dict] | None:
    """The row with the lowest recall, among rows with enough tickets to mean something."""
    rows = [(name, row) for name, row in table.items() if row[count] >= 3 and row["recall"] is not None]
    return min(rows, key=lambda item: item[1]["recall"]) if rows else None


def num(value, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def rows(c: dict) -> list[tuple[str, str, str, str]]:
    """Step, what is measured, the value, and what it was counted over."""
    cl, re, pl, gu, ge, fa = c["classifier"], c["retriever"], c["planner"], c["guardrail"], c["generator"], c["faults"]
    out = [("classifier", "Intent right", num(cl["accuracy"]), f"{cl['right']} of {cl['tickets']}")]
    low = weakest(cl["per_intent"], "labelled")
    if low:
        out.append(("classifier", f"Weakest intent, {low[0]}", num(low[1]["recall"]), f"{low[1]['right']} of {low[1]['labelled']}"))
    out += [
        ("retriever", "Every required policy shown to the plan", num(re["recall"]), f"{re['complete']} of {re['tickets']}"),
        ("retriever", "Share of searched sections from a required policy", num(re["precision"]),
         f"{num(re['sections_searched'], 0)} sections a ticket"),
        ("retriever", "Mean reciprocal rank of the first required section", num(re["mrr"]), f"{re['ranked_tickets']} tickets"),
    ]
    if re["only_by_the_pin"]:
        out.append(("retriever", "A required policy was there only because it is pinned", str(re["only_by_the_pin"]),
                    f"{re['tickets']} tickets"))
    low = weakest(re["per_policy"], "needed")
    if low:
        out.append(("retriever", f"Weakest policy, {low[0]}", num(low[1]["recall"]), f"{low[1]['found']} of {low[1]['needed']}"))
    which = "First plan" if pl["first_plan_recorded"] else "Last plan"
    out += [
        ("planner", f"{which} matches the label", num(pl["accuracy"]), f"{pl['right']} of {pl['tickets']}"),
        ("planner", "The same, intent and policies both right", num(pl["inputs_right"]["accuracy"]),
         f"{pl['inputs_right']['right']} of {pl['inputs_right']['tickets']}"),
        ("planner", "The same, one of them wrong", num(pl["an_input_wrong"]["accuracy"]),
         f"{pl['an_input_wrong']['right']} of {pl['an_input_wrong']['tickets']}"),
        ("planner", "Sent to a person needlessly", str(pl["sent_to_a_person_needlessly"]), "tickets labelled answer or act"),
        ("planner", "Handled what needed a person", str(pl["handled_what_needed_a_person"]), "tickets labelled escalate"),
        ("guardrail", "Actions refused", str(gu["denied"]), f"{gu['tickets']} actions proposed"),
        ("guardrail", "Refused where the label wanted no action", str(len(gu["denied_no_action_wanted"])), f"{gu['denied']} refusals"),
        ("generator", "First draft passed every check", num(ge["first_draft_pass_rate"]), f"{ge['first_draft_passed']} of {ge['tickets']}"),
        ("generator", "Sentences the checker found supported", num(ge["sentence_support"]),
         f"{ge['sentences_supported']} of {ge['sentences_checked']}"),
        ("generator", "Drafts written per ticket", num(ge["drafts_per_ticket"]), f"{ge['tickets']} tickets"),
        ("retriever to generator", "First draft passed, policies complete", num(ge["pass_rate_policies_complete"]),
         f"{ge['tickets'] - ge['tickets_a_policy_missing']} tickets"),
        ("retriever to generator", "First draft passed, a policy missing", num(ge["pass_rate_a_policy_missing"]),
         f"{ge['tickets_a_policy_missing']} tickets"),
    ]
    for name in FAULTS:
        out.append(("first fault", name, str(fa["first_fault"][name]), f"{fa['lost']} tickets lost of {fa['should_close']}"))
    return out


def markdown(c: dict, meta: dict, source: str) -> str:
    lines = [f"From `{source}`: {model_cell(meta)}, checker {checker_cell(meta, default=True)}. Built on {date.today().isoformat()}.",
             "", "| Step | What is measured | Value | Counted over |", "| --- | --- | --- | --- |"]
    lines += [f"| {step} | {what} | {value} | {over} |" for step, what, value, over in rows(c)]
    mixed = c["classifier"]["stand_ins"] or c["generator"]["stand_ins"]
    if mixed:
        lines += ["", f"A stand in classified {c['classifier']['stand_ins']} tickets and checked {c['generator']['stand_ins']} "
                      "drafts in this run, so the classifier and generator rows describe a mix."]
    return "\n".join(lines)


def show(c: dict) -> None:
    width = max(len(what) for _, what, _, _ in rows(c))
    step_now = None
    for step, what, value, over in rows(c):
        if step != step_now:
            print(f"\n  {step}")
            step_now = step
        print(f"    {what:<{width}}  {value:>5}   {over}")
    print("\n  intents:  " + ", ".join(f"{name} {row['right']} of {row['labelled']}" for name, row in c["classifier"]["per_intent"].items()))
    print("  policies: " + ", ".join(f"{name} {row['found']} of {row['needed']}" for name, row in c["retriever"]["per_policy"].items()))
    print("  plans:    " + ", ".join(f"{what} {n}" for what, n in c["planner"]["chose"]))
    print("  draft faults: " + (", ".join(f"{k} {v}" for k, v in c["generator"]["first_draft_faults"].items()) or "none"))
    for miss in c["retriever"]["misses"]:
        print(f"    missed {', '.join(miss['missing'])} on {miss['case_id']}{'' if miss['intent_right'] else ', intent was wrong'}")


def write(path: Path, block: str) -> None:
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        raise SystemExit(f"{path} has no component level markers, so the table has nowhere to go")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    path.write_text(f"{before}{START}\n{block}\n{END}{after}", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Score each step of the graph from a recorded run.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--out", type=Path, help="also write every number and ticket list as JSON")
    parser.add_argument("--write", action="store_true", help="replace the component level table in EVALS.md")
    parser.add_argument("--file", type=Path, default=EVALS_FILE)
    args = parser.parse_args()

    try:
        results = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Cannot read {args.results}: {exc}", file=sys.stderr)
        return 2
    numbers = compute(results["cases"])
    meta = results["meta"]
    print(f"Component scores for {args.results.name}: {model_cell(meta)}, checker {checker_cell(meta, default=True)}")
    show(numbers)
    if numbers["classifier"]["stand_ins"] or numbers["generator"]["stand_ins"]:
        print(f"\n  A stand in classified {numbers['classifier']['stand_ins']} tickets and checked "
              f"{numbers['generator']['stand_ins']} drafts, so those two steps are a mix in this run.", file=sys.stderr)
    if args.out:
        args.out.write_text(json.dumps({"results": args.results.name, "meta": meta, "components": numbers}, indent=2), encoding="utf-8")
        print(f"\nWrote {args.out}")
    if args.write:
        write(args.file, markdown(numbers, meta, args.results.name))
        print(f"\nWrote the component table into {args.file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
