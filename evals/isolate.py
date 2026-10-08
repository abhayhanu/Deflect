"""Runs one step of the graph on its own, with every step before it replaced by the answer key.

A recorded run scores a step on whatever the steps before it produced. Here the inputs are
right by construction, so a wrong output can only be that step's own mistake.

    retriever   the real search over the golden messages. No model is called.
    planner     the real plan node, given the labelled intent and the order as it was seeded
    generator   the real draft node, given the labelled decision as well

The planner and the generator read policy excerpts, and where those come from is a second
choice. With oracle context they get every section of the policies the label requires. With
retrieved context they get what the real search returns for the labelled intent. The first is
the step alone. The second is the retriever and that step as a pipeline. The gap between the
two is what retrieval costs that step.

A draft is always scored against the oracle context, whichever context it was written from.
Otherwise a reply written faithfully from the wrong policy would count as a good one.

What stands in for the steps that are skipped:

    classify    the labelled intent and the labelled order id, urgency medium, sentiment neutral
    plan        for the generator, the labelled decision citing the required policies, with a
                fixed note where the planner's own reasoning would be
    act         for the generator, the action a recorded run took, and only on tickets where
                that run took the labelled action. Without a recorded run, only answers are written.

Nothing here changes the database, and nothing from it goes in the version tables. A version
is a full run. This says which step to change before the next one.
"""

import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from agent.context import RunContext
from agent.guardrails.audit import MemoryAuditLog
from agent.guardrails.policy import CHECKER_UNSUPPORTED_AT, early_escalation
from agent.guardrails.redact import redact
from agent.mcp_client import MCPClient, TransportError
from agent.mcp_client import connect as connect_tools
from agent.nodes.draft import draft
from agent.nodes.plan import plan
from agent.nodes.retrieve import QUERY_LIMIT, retrieve
from agent.nodes.verify import TEMPLATE_SLOT, check_with_model, unbacked_claims, wrong_timelines
from agent.providers import DEFAULT_MODELS, checker_name, get_chat_model, get_checker
from agent.state import Classification, Plan, RetrievedPolicy, ToolCall
from data.db import connect
from data.index_policies import ensure_indexed, load_chunks
from data.migrate import pending as pending_migrations
from data.queries import count_tool_requests, fetch_seed_anchor
from evals.metrics import action_correct, percentile, ratio, same_value
from evals.run import SECTION, load_cases, reseed
from evals.schema import GoldenCase

log = logging.getLogger("evals.isolate")

DEPTHS = (1, 3, 5, 8, 10)
SEARCH_DEPTH = 10
ORACLE_NOTE = ("This decision comes from the label and not from the planner. Write the reply from the policy excerpts, "
               "the order details and the action result.")
CONTEXTS = ("oracle", "retrieved")


def oracle_policies(case: GoldenCase, chunks: list) -> list[RetrievedPolicy]:
    """Every section of every policy the label requires."""
    return [RetrievedPolicy(doc_id=c.doc_id, title=c.title, chunk=c.text, score=1.0)
            for c in chunks if c.doc_id in case.expected.required_policy_ids]


def labelled_state(case: GoldenCase, anchor: datetime) -> dict:
    """The state as a perfect classifier would leave it."""
    classification = Classification(intent=case.expected.intent, confidence=1.0, urgency="medium", sentiment="neutral",
                                    order_id=case.order_id, reasoning="From the label.")
    return {"ticket_id": case.case_id, "raw_message": case.raw_message, "redacted_message": redact(case.raw_message)[0],
            "customer_id": case.customer_id, "channel": case.channel, "received_at": anchor, "classification": classification}


def as_runtime(ctx: RunContext) -> SimpleNamespace:
    """A node only ever reads the context from its runtime."""
    return SimpleNamespace(context=ctx)


def complete(case: GoldenCase, policies: list[RetrievedPolicy]) -> bool:
    return set(case.expected.required_policy_ids) <= {p.doc_id for p in policies}


# The retriever

def search(tools: MCPClient, case: GoldenCase, intent: str | None) -> tuple[list[str], list[str]]:
    """The policy and section of every hit for the query the retrieve node sends: what the
    search ranked, best first, and then what came back only because its policy is pinned."""
    args = {"query": redact(case.raw_message)[0][:QUERY_LIMIT], "top_k": SEARCH_DEPTH}
    if intent:
        args["intent"] = intent
    outcome = tools.call("search_policy", args)
    if outcome.error:
        raise RuntimeError(f"search_policy failed: {outcome.error}")
    ranked, pinned = [], []
    for hit in outcome.result:
        heading = SECTION.search(hit["chunk"])
        (pinned if hit.get("pinned") else ranked).append(f"{hit['doc_id']}#{heading.group(1).strip() if heading else '?'}")
    return ranked, pinned


def ranking_scores(rows: list[dict]) -> dict:
    """rows hold the required policies, the ranked hits and the pinned sections of one ticket
    each. A pinned policy counts as found at every depth, because the plan is shown it whatever
    the search does. Precision and rank are about the search alone, so they leave it out."""
    def docs(row, depth):
        return [hit.split("#")[0] for hit in row["ranked"][:depth]]

    def given(row):
        return {hit.split("#")[0] for hit in row.get("pinned") or []}

    def shown(row, depth):
        return set(docs(row, depth)) | given(row)

    def to_find(row):
        return [doc for doc in row["required"] if doc not in given(row)]

    def first_rank(row):
        return next((n for n, doc in enumerate(docs(row, SEARCH_DEPTH), start=1) if doc in to_find(row)), None)

    by_policy: dict[str, list[int]] = {}
    for row in rows:
        for doc in row["required"]:
            found = by_policy.setdefault(doc, [0, 0])
            found[0] += doc in shown(row, 5)
            found[1] += 1
    searched = [r for r in rows if to_find(r)]
    return {
        "tickets": len(rows),
        "recall_at": {str(k): ratio(sum(set(r["required"]) <= shown(r, k) for r in rows), len(rows)) for k in DEPTHS},
        "precision_at_5": round(sum(sum(d in to_find(r) for d in docs(r, 5)) / max(1, len(docs(r, 5))) for r in searched) / len(searched), 4)
        if searched else None,
        "mrr": round(sum(1 / rank if (rank := first_rank(r)) else 0 for r in searched) / len(searched), 4) if searched else None,
        "per_policy_at_5": {doc: {"needed": n, "found": hit, "recall": ratio(hit, n)} for doc, (hit, n) in sorted(by_policy.items())},
        "misses_at_5": sorted(r["case_id"] for r in rows if not set(r["required"]) <= shown(r, 5)),
        "pinned": sorted({doc for r in rows for doc in given(r)}),
    }


def run_retriever(cases: list[GoldenCase], tools: MCPClient, recorded: dict | None) -> dict:
    """The same search under each way of choosing the intent filter. The gap between the
    labelled intent and no filter is what the filter is worth. The gap between the labelled
    intent and the intent a recorded run predicted is what the classifier costs the search."""
    cases = [c for c in cases if c.expected.required_policy_ids]
    said = {r["case_id"]: r["predicted"]["intent"] for r in recorded["cases"]} if recorded else {}
    filters = {"labelled intent": lambda c: c.expected.intent, "no intent filter": lambda c: None}
    if recorded:
        filters["intent the recorded run predicted"] = lambda c: said.get(c.case_id)
    tickets, summary = [], {}
    for name, pick in filters.items():
        chosen = [c for c in cases if name != "intent the recorded run predicted" or said.get(c.case_id)]
        rows = []
        for c in chosen:
            ranked, pinned = search(tools, c, pick(c))
            rows.append({"case_id": c.case_id, "filter": name, "intent": pick(c), "required": c.expected.required_policy_ids,
                         "ranked": ranked, "pinned": pinned})
        summary[name] = ranking_scores(rows)
        tickets += rows
    return {"summary": summary, "tickets": tickets}


def show_retriever(summary: dict) -> None:
    for name, s in summary.items():
        depths = ", ".join(f"top {k} {v:.2f}" for k, v in s["recall_at"].items())
        print(f"\n  {name}: {s['tickets']} tickets")
        print(f"    every required policy found   {depths}")
        print(f"    precision in the top 5 {s['precision_at_5']:.2f}, mean reciprocal rank {s['mrr']:.2f}")
        if s.get("pinned"):
            print(f"    always shown, so never searched for: {', '.join(s['pinned'])}")
        weak = [f"{doc} found {row['found']} of {row['needed']}" for doc, row in s["per_policy_at_5"].items() if row["found"] < row["needed"]]
        print(f"    policies not always in the top 5: {', '.join(weak) or 'none'}")
        print(f"    tickets with one missing: {' '.join(s['misses_at_5']) or 'none'}")


# The planner

def plan_ticket(case: GoldenCase, state: dict, ctx: RunContext) -> dict:
    made = plan(state, as_runtime(ctx))["plan"]
    expected = case.expected
    args = {k: v for k, v in (made.tool_args or {}).items() if k != "idempotency_key"}
    right_action = None
    if expected.decision == "act":
        right_action = (made.decision == "act" and made.tool_name == expected.tool_name
                        and all(same_value(v, args.get(k)) for k, v in (expected.tool_args or {}).items()))
    return {"decision": made.decision, "tool_name": made.tool_name, "tool_args": args, "cites": made.cites,
            "escalation_reason": made.escalation_reason, "rationale": made.rationale,
            "right": made.decision == expected.decision, "right_action": right_action,
            "cites_required": set(expected.required_policy_ids) <= set(made.cites)}


def planner_scores(rows: list[dict]) -> dict:
    by_label = {}
    for decision in ("answer", "act", "escalate"):
        labelled = [r for r in rows if r["expected_decision"] == decision]
        by_label[decision] = {"labelled": len(labelled), "right": sum(r["right"] for r in labelled),
                              "recall": ratio(sum(r["right"] for r in labelled), len(labelled))}
    acts = [r for r in rows if r["expected_decision"] == "act"]
    whole = [r for r in rows if r["policies_complete"]]
    short = [r for r in rows if not r["policies_complete"]]
    return {
        "tickets": len(rows),
        "right": sum(r["right"] for r in rows),
        "accuracy": ratio(sum(r["right"] for r in rows), len(rows)),
        "by_label": by_label,
        "right_action": ratio(sum(bool(r["right_action"]) for r in acts), len(acts)),
        "sent_to_a_person_needlessly": sorted(r["case_id"] for r in rows if r["expected_decision"] != "escalate" and r["decision"] == "escalate"),
        "handled_what_needed_a_person": sorted(r["case_id"] for r in rows if r["expected_decision"] == "escalate" and r["decision"] != "escalate"),
        "unusable_plans": sum(r["escalation_reason"] in ("parse_failure", "act_without_tool") for r in rows),
        "accuracy_policies_complete": ratio(sum(r["right"] for r in whole), len(whole)),
        "accuracy_a_policy_missing": ratio(sum(r["right"] for r in short), len(short)),
        "tickets_a_policy_missing": len(short),
    }


def show_planner(name: str, s: dict) -> None:
    print(f"\n  {name} context: {s['right']} of {s['tickets']} decisions match the label, {s['accuracy']}")
    print("    " + ", ".join(f"{label} {row['right']} of {row['labelled']}" for label, row in s["by_label"].items()))
    print(f"    right tool and arguments on act tickets {s['right_action']}, plans that could not be used {s['unusable_plans']}")
    print(f"    sent to a person needlessly {len(s['sent_to_a_person_needlessly'])}")
    print(f"    handled what needed a person {len(s['handled_what_needed_a_person'])}: {' '.join(s['handled_what_needed_a_person']) or 'none'}")
    if s["tickets_a_policy_missing"]:
        print(f"    with every required policy in view {s['accuracy_policies_complete']}, "
              f"with one missing {s['accuracy_a_policy_missing']} on {s['tickets_a_policy_missing']} tickets")


# The generator

def recorded_actions(record: dict | None, anchor: datetime) -> list[ToolCall] | None:
    """The actions a recorded run took on this ticket, if it took the labelled one."""
    if not record or not action_correct(record):
        return None
    return [ToolCall(name=t["name"], args=t["args"], result=t["result"], error=t["error"], authorized_by=t["authorized_by"],
                     latency_ms=t["latency_ms"], called_at=anchor, approver_id=t.get("approver_id"))
            for t in record["tool_calls"] if not t["error"]]


def labelled_plan(case: GoldenCase) -> Plan:
    expected = case.expected
    return Plan(decision=expected.decision, tool_name=expected.tool_name, tool_args=expected.tool_args,
                cites=expected.required_policy_ids, rationale=ORACLE_NOTE)


def write_ticket(state: dict, truth: list[RetrievedPolicy], ctx: RunContext) -> dict:
    text = draft(state, as_runtime(ctx))["draft"]
    scored = {**state, "policies": truth, "draft": text}
    faults = {"claims_unperformed_action": unbacked_claims(text, scored), "wrong_refund_timeline": wrong_timelines(text, scored),
              "template_slot": TEMPLATE_SLOT.findall(text)}
    # In the graph the model only reads a draft the rules passed. Here it reads every draft,
    # so each one gets a faithfulness number.
    flagged, checked = check_with_model(scored, ctx)
    doubt = checked.get("sentence_doubt") or {}
    return {"draft": text, "rule_faults": {k: v for k, v in faults.items() if v}, "unsupported": flagged,
            "sentence_doubt": doubt, "sentences": len(doubt), "supported": sum(p < CHECKER_UNSUPPORTED_AT for p in doubt.values()),
            "checked_by": checked.get("checked_by"), "checker_fell_back": bool(checked.get("checker_fell_back")),
            "passed": not any(faults.values()) and not flagged}


def generator_scores(rows: list[dict]) -> dict:
    whole = [r for r in rows if r["policies_complete"]]
    short = [r for r in rows if not r["policies_complete"]]
    sentences = sum(r["sentences"] for r in rows)
    return {
        "tickets": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "pass_rate": ratio(sum(r["passed"] for r in rows), len(rows)),
        "by_label": {d: {"tickets": len(g), "passed": sum(r["passed"] for r in g)}
                     for d in ("answer", "act") if (g := [r for r in rows if r["expected_decision"] == d])},
        "faults": dict(Counter(k for r in rows for k in [*r["rule_faults"], *(["unsupported_claims"] if r["unsupported"] else [])])),
        "sentences_checked": sentences,
        "sentence_support": ratio(sum(r["supported"] for r in rows), sentences),
        "pass_rate_policies_complete": ratio(sum(r["passed"] for r in whole), len(whole)),
        "pass_rate_a_policy_missing": ratio(sum(r["passed"] for r in short), len(short)),
        "tickets_a_policy_missing": len(short),
        "checker_stand_ins": sum(r["checker_fell_back"] for r in rows),
        "failed": sorted(r["case_id"] for r in rows if not r["passed"]),
    }


def show_generator(name: str, s: dict) -> None:
    print(f"\n  {name} context: {s['passed']} of {s['tickets']} drafts pass every check, {s['pass_rate']}")
    print("    " + ", ".join(f"{label} {row['passed']} of {row['tickets']}" for label, row in s["by_label"].items()))
    print(f"    sentences the checker found supported {s['sentence_support']} of {s['sentences_checked']}")
    print(f"    faults: {', '.join(f'{k} {v}' for k, v in s['faults'].items()) or 'none'}")
    if s["tickets_a_policy_missing"]:
        print(f"    with every required policy in view {s['pass_rate_policies_complete']}, "
              f"with one missing {s['pass_rate_a_policy_missing']} on {s['tickets_a_policy_missing']} tickets")
    if s["checker_stand_ins"]:
        print(f"    {s['checker_stand_ins']} drafts were checked by a stand in, so the checker's part of this is a mix")


# Both model steps

def run_ticket(step: str, case: GoldenCase, anchor: datetime, tools: MCPClient, chunks: list, contexts: list[str],
               actions: list[ToolCall] | None) -> list[dict]:
    """One row per context. The search and the order lookup run once and both contexts share them."""
    # The audit log stays in memory, so an isolated run leaves no rows behind.
    ctx = RunContext(now=anchor, tools=tools, audit=MemoryAuditLog())
    state = labelled_state(case, anchor)
    rows = []
    try:
        state.update(retrieve(state, as_runtime(ctx)))
        found = {"oracle": oracle_policies(case, chunks), "retrieved": state["policies"]}
        for name in contexts:
            started, spent = time.perf_counter(), ctx.usage.cost_inr
            given = {**state, "policies": found[name]}
            if step == "planner":
                result = plan_ticket(case, given, ctx)
            else:
                given.update(plan=labelled_plan(case), tool_calls=actions or [])
                result = write_ticket(given, found["oracle"], ctx)
            rows.append({"case_id": case.case_id, "context": name, "expected_decision": case.expected.decision,
                         "policies_complete": complete(case, found[name]), "error": None, **result,
                         "cost_inr": ctx.usage.cost_inr - spent, "latency_ms": round((time.perf_counter() - started) * 1000)})
    except Exception as exc:
        log.warning("%s crashed on %s: %s", step, case.case_id, exc, exc_info=log.isEnabledFor(logging.INFO))
        done = {r["context"] for r in rows}
        rows += [{"case_id": case.case_id, "context": name, "expected_decision": case.expected.decision, "policies_complete": False,
                  "error": f"{type(exc).__name__}: {str(exc)[:200]}"} for name in contexts if name not in done]
    return rows


def chosen_cases(step: str, cases: list[GoldenCase], records: dict[str, dict], anchor: datetime) -> list[tuple[GoldenCase, list | None]]:
    """The tickets a step can be given, each with the actions the writer may lean on."""
    if step == "planner":
        return [(c, None) for c in cases if early_escalation(labelled_state(c, anchor)) is None]
    picked = []
    for c in cases:
        actions = recorded_actions(records.get(c.case_id), anchor) if c.expected.decision == "act" else None
        if c.expected.decision == "answer" or actions:
            picked.append((c, actions))
    return picked


def gap(rows: list[dict], good: str) -> dict:
    """Tickets where the two contexts disagree. Right with oracle and wrong with retrieved is
    what the retriever cost this step."""
    by_ticket: dict[str, dict] = {}
    for r in rows:
        if not r["error"]:
            by_ticket.setdefault(r["case_id"], {})[r["context"]] = r[good]
    both = {case_id: seen for case_id, seen in by_ticket.items() if len(seen) == 2}
    return {"tickets_in_both": len(both),
            "lost_to_retrieval": sorted(c for c, seen in both.items() if seen["oracle"] and not seen["retrieved"]),
            "better_with_retrieved": sorted(c for c, seen in both.items() if seen["retrieved"] and not seen["oracle"])}


def database_ready(do_reseed: bool, anchor_arg: str | None) -> datetime | None:
    if do_reseed:
        reseed(anchor_arg)
    with connect() as conn:
        anchor, writes, missing = fetch_seed_anchor(conn), count_tool_requests(conn), pending_migrations(conn)
    if anchor is None or writes is None or missing:
        print("The database is not seeded. Run again with --reseed.", file=sys.stderr)
        return None
    if writes:
        print(f"The database holds {writes} actions from an earlier run, so the orders are no longer as labelled. "
              "Run again with --reseed.", file=sys.stderr)
        return None
    return anchor


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one step of the graph with the steps before it replaced by the labels.")
    parser.add_argument("step", choices=["retriever", "planner", "generator"])
    parser.add_argument("--context", choices=[*CONTEXTS, "both"], default="both",
                        help="where the planner or the generator gets its policy excerpts. both runs each ticket twice")
    parser.add_argument("--recorded", type=Path, help="a results file. Gives the retriever the intents that run predicted, "
                                                      "and the generator the actions that run took")
    parser.add_argument("--subset", choices=["smoke", "full", "adversarial", "regression"], default="full")
    parser.add_argument("--case", action="append", help="only this case id, can be repeated")
    parser.add_argument("--provider", choices=list(DEFAULT_MODELS), help="overrides DEFLECT_PROVIDER for this run")
    parser.add_argument("--model", help="overrides DEFLECT_MODEL for this run")
    parser.add_argument("--checker", help="overrides DEFLECT_CHECKER_PROVIDER. agent means the agent's own model")
    parser.add_argument("--workers", type=int, default=1, help="tickets run in parallel, keep 1 for Ollama")
    parser.add_argument("--reseed", action="store_true", help="reset the shop data first")
    parser.add_argument("--anchor", help="with --reseed, the time the data treats as now. Defaults to DEFLECT_SEED_ANCHOR")
    parser.add_argument("--out", type=Path)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    out = args.out or Path(f"isolate_{args.step}.json")

    if args.provider:
        os.environ["DEFLECT_PROVIDER"] = args.provider
        os.environ["DEFLECT_MODEL"] = args.model or DEFAULT_MODELS[args.provider]
    elif args.model:
        os.environ["DEFLECT_MODEL"] = args.model
    if args.checker:
        os.environ["DEFLECT_CHECKER_PROVIDER"] = "" if args.checker == "agent" else args.checker
        os.environ["DEFLECT_CHECKER_MODEL"] = ""
    recorded = json.loads(args.recorded.read_text(encoding="utf-8")) if args.recorded else None
    cases = load_cases(args.subset, args.case)

    ensure_indexed()
    try:
        tools = connect_tools(pin_clock=True)
        tools.start()
    except TransportError as exc:
        print(f"Could not start the MCP server: {exc}", file=sys.stderr)
        return 2

    if args.step == "retriever":
        print(f"Searching for {sum(bool(c.expected.required_policy_ids) for c in cases)} tickets, {SEARCH_DEPTH} hits each")
        with tools:
            result = run_retriever(cases, tools, recorded)
        show_retriever(result["summary"])
        out.write_text(json.dumps({"step": "retriever", "recorded": args.recorded.name if args.recorded else None, **result},
                                  indent=2), encoding="utf-8")
        print(f"\nWrote {out}")
        return 0

    model = get_chat_model("agent")
    if args.step == "generator":
        try:
            get_checker(model)
        except (ImportError, ValueError) as exc:
            print(f"Cannot start the reply checker: {exc}", file=sys.stderr)
            return 2
    anchor = database_ready(args.reseed, args.anchor)
    if anchor is None:
        return 2
    contexts = list(CONTEXTS) if args.context == "both" else [args.context]
    records = {r["case_id"]: r for r in recorded["cases"]} if recorded else {}
    chosen = chosen_cases(args.step, cases, records, anchor)
    chunks = load_chunks()
    scored_by = f", drafts checked by {checker_name(model)}" if args.step == "generator" else ""
    print(f"Running the {args.step} alone on {len(chosen)} tickets with {model.provider}:{model.name}{scored_by}, "
          f"context {' and '.join(contexts)}, clock pinned to {anchor:%Y-%m-%d %H:%M} UTC")

    rows = []
    with tools, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        work = pool.map(lambda item: run_ticket(args.step, item[0], anchor, tools, chunks, contexts, item[1]), chosen)
        for n, ticket_rows in enumerate(work, start=1):
            rows += ticket_rows
            said = "  ".join("ERROR" if r["error"] else f"{r['context']} {r['decision'] if args.step == 'planner' else ('pass' if r['passed'] else 'fail')}"
                             for r in ticket_rows)
            print(f"  [{n:>3}/{len(chosen)}] {ticket_rows[0]['case_id']}  label {ticket_rows[0]['expected_decision']}  {said}")

    ok = [r for r in rows if not r["error"]]
    crashed = len(rows) - len(ok)
    score, show, good = (planner_scores, show_planner, "right") if args.step == "planner" else (generator_scores, show_generator, "passed")
    summary = {name: score([r for r in ok if r["context"] == name]) for name in contexts}
    between = gap(rows, good) if len(contexts) == 2 else None
    latencies = [r["latency_ms"] for r in ok]
    out.write_text(json.dumps({
        "step": args.step, "model": f"{model.provider}:{model.name}", "checker": checker_name(model) if args.step == "generator" else None,
        "subset": args.subset if not args.case else "custom", "recorded": args.recorded.name if args.recorded else None,
        "seed_anchor": anchor.isoformat(), "crashed": crashed, "summary": summary, "gap": between,
        "latency_ms_p50": percentile(latencies, 50), "cost_inr": round(sum(r["cost_inr"] for r in ok), 4), "tickets": rows},
        indent=2, default=str), encoding="utf-8")

    if crashed:
        first = next(r["error"] for r in rows if r["error"])
        print(f"\n  {crashed} runs crashed and are left out of the scores, the first error was: {first}", file=sys.stderr)
    for name in contexts:
        show(name, summary[name])
    if between:
        print(f"\n  The retriever and the {args.step} as a pipeline, {between['tickets_in_both']} tickets run both ways:")
        print(f"    right with oracle context, wrong with retrieved: {len(between['lost_to_retrieval'])}  {' '.join(between['lost_to_retrieval'])}")
        print(f"    wrong with oracle context, right with retrieved: {len(between['better_with_retrieved'])}  {' '.join(between['better_with_retrieved'])}")
    print(f"\nWrote {out}. Nothing from an isolated run goes in the version tables.")
    return 1 if rows and crashed == len(rows) else 0


if __name__ == "__main__":
    sys.exit(main())
