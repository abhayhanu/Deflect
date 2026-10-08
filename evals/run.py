"""Runs the golden tickets through the agent and writes every outcome plus the metrics to JSON.

The agent's clock is pinned to the seed anchor, so "delivered 47 hours ago" stays true
however long the run takes. The MCP server it starts is pinned to the same clock.

From Phase 2 the agent really refunds and cancels, so a run changes the database. A second run
on the same data would meet orders that are already refunded, so the runner refuses to start
until the database is reseeded. The reseed option does that for you.

From Phase 3 a refund above the auto approve ceiling pauses for a person. The runner plays
that person. By default it approves only an action that matches the label exactly, like a
careful reviewer who knows the right answer, and denies anything else. Every request and
decision is recorded, so approval routing is measured, not assumed.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from agent import tracing
from agent.approvals import decide_and_resume
from agent.context import RunContext
from agent.graph import build_graph, memory_checkpointer
from agent.guardrails.policy import escalation_signals
from agent.mcp_client import MCPClient, TransportError
from agent.mcp_client import connect as connect_tools
from agent.nodes.verify import TEMPLATE_SLOT, unbacked_claims, wrong_timelines
from agent.providers import DEFAULT_MODELS, checker_name, classifier_name, get_chat_model, get_checker, get_classifier
from data.db import connect
from data.index_policies import ensure_indexed
from data.migrate import pending as pending_migrations
from data.queries import count_tool_requests, fetch_seed_anchor
from evals.metrics import compute, same_value
from evals.schema import GoldenCase
from evals.validate import GOLDEN_FILE

APPROVER_ID = "eval_harness"
MAX_APPROVAL_ROUNDS = 3
SECTION = re.compile(r"^## (.+)$", re.MULTILINE)

log = logging.getLogger("evals.run")


def load_cases(subset: str, only: list[str] | None = None) -> list[GoldenCase]:
    cases = [GoldenCase.model_validate_json(line) for line in GOLDEN_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if only:
        return [c for c in cases if c.case_id in only]
    if subset == "escalation":
        # Every ticket that must end with a person. Small enough to run several times over.
        return [c for c in cases if c.expected.must_escalate]
    return [c for c in cases if subset == "full" or subset in c.tags]


def reply_checks(final: dict) -> dict:
    """The checker's deterministic rules, applied to the reply that was actually sent."""
    reason = final.get("terminal_reason") or ""
    if reason not in ("answered", "acted") or not final.get("draft"):
        return {"unbacked_action_claims": [], "template_slots": [], "wrong_timelines": []}
    return {"unbacked_action_claims": unbacked_claims(final["draft"], final),
            "template_slots": TEMPLATE_SLOT.findall(final["draft"]),
            "wrong_timelines": wrong_timelines(final["draft"], final)}


def sections(final: dict, pinned_only: bool = False) -> list[str]:
    """Which section of which policy the plan was shown, so a checker verdict can be read
    against exactly what the checker saw. With pinned_only, just the sections that were there
    because their policy is pinned and the search had not ranked them."""
    found = []
    for p in final.get("policies", []):
        if pinned_only and not p.pinned:
            continue
        heading = SECTION.search(p.chunk)
        found.append(f"{p.doc_id}#{heading.group(1).strip() if heading else '?'}")
    return found


def outcome(case: GoldenCase, final: dict, ctx: RunContext, latency_ms: int, error: str | None,
            approvals: list[dict], rounds: list[dict] | None = None, plans: list[dict] | None = None) -> dict:
    c = final.get("classification")
    plan = final.get("plan")
    guard = final.get("guardrail")
    check = final.get("verification")
    escalated = (final.get("terminal_reason") or "").startswith("escalated")
    return {
        "case_id": case.case_id,
        "tags": case.tags,
        "expected": {
            "intent": case.expected.intent,
            "decision": case.expected.decision,
            "must_escalate": case.expected.must_escalate,
            "required_policy_ids": case.expected.required_policy_ids,
            "tool_name": case.expected.tool_name,
            "tool_args": case.expected.tool_args,
            "requires_approval": case.expected.requires_approval,
            "forbidden_tools": case.expected.forbidden_tools,
        },
        "predicted": {
            "intent": c.intent if c else None,
            "confidence": c.confidence if c else None,
            "order_id": c.order_id if c else None,
            "classified_by": final.get("classified_by"),
            "classifier_fell_back": bool(final.get("classifier_fell_back")),
            "intent_probabilities": final.get("intent_probabilities") or {},
            "decision": "escalate" if escalated else (plan.decision if plan else None),
            "escalated": escalated,
            "cites": plan.cites if plan else [],
            "retrieved_ids": sorted({p.doc_id for p in final.get("policies", [])}),
            "retrieved_sections": sections(final),
            "pinned_sections": sections(final, pinned_only=True),
            "terminal_reason": final.get("terminal_reason"),
            # The pol_escalation rules the message itself matches. Such a ticket must end with a person.
            "message_signals": [s.rule for s in escalation_signals(case.raw_message)],
            "plan_decision": plan.decision if plan else None,
            "escalation_reason": plan.escalation_reason if plan else None,
            "rationale": plan.rationale if plan else None,
        },
        "tool_calls": [
            {"name": t.name, "args": t.args, "result": t.result, "error": t.error, "latency_ms": t.latency_ms,
             "authorized_by": t.authorized_by, "approver_id": t.approver_id}
            for t in final.get("tool_calls", [])
        ],
        # The order as retrieve read it, before any action. It holds no personal details.
        "order": final.get("order"),
        "guardrail": guard.model_dump(exclude={"action_key"}) if guard else None,
        "approval": {"requested": bool(approvals), "rounds": approvals,
                     "decision": approvals[-1]["decision"] if approvals else None},
        "verification": check.model_dump() if check else None,
        "checker_rounds": rounds or [],
        "plan_rounds": plans or [],
        "retry_count": final.get("retry_count", 0),
        "loop_count": final.get("loop_count", 0),
        "escalation_case": (final.get("escalation") or {}).get("case"),
        "reply_checks": reply_checks(final),
        "reply": final.get("reply"),
        "cost_inr": final.get("cost_inr", 0.0),
        "latency_ms": latency_ms,
        "parse_failures": ctx.usage.parse_failures,
        "structured_calls": ctx.usage.structured_calls,
        "input_tokens": ctx.usage.input_tokens,
        "output_tokens": ctx.usage.output_tokens,
        "trace_id": final.get("trace_id"),
        "error": error,
    }


def approver_says_yes(mode: str, case: GoldenCase, request: dict) -> bool:
    if mode != "label":
        return mode == "approve"
    expected = case.expected
    return (expected.decision == "act" and request["tool_name"] == expected.tool_name
            and all(same_value(v, request["tool_args"].get(k)) for k, v in (expected.tool_args or {}).items()))


def plan_rounds(history: list) -> list[dict]:
    """Every plan the ticket made, oldest first. A reply the checker sends back can lead to a
    second plan, and the first one is what the planner chose with nothing to correct it."""
    rounds = []
    for before, after in zip(history, history[1:]):
        made = after.values.get("plan")
        if before.next == ("plan",) and made is not None:
            rounds.append({"decision": made.decision, "tool_name": made.tool_name,
                           "tool_args": {k: v for k, v in (made.tool_args or {}).items() if k != "idempotency_key"},
                           "cites": made.cites, "escalation_reason": made.escalation_reason})
    return rounds


def checker_rounds(history: list) -> list[dict]:
    """Every verdict the checker gave, oldest first. The state only keeps the last one, and a
    reply that passed on its third try says nothing about what was wrong with the first two."""
    rounds = []
    for before, after in zip(history, history[1:]):
        check = after.values.get("verification")
        if before.next == ("verify",) and check is not None:
            rounds.append({"verdict": check.verdict, "failed_checks": check.failed_checks,
                           "unsupported_claims": check.unsupported_claims, "draft": after.values.get("draft"),
                           "checked_by": check.checked_by, "checker_fell_back": check.checker_fell_back,
                           "sentence_doubt": check.sentence_doubt})
    return rounds


def run_case(graph, tools: MCPClient, case: GoldenCase, anchor: datetime, run_id: str, approver: str = "label") -> dict:
    ctx = RunContext(now=anchor, tools=tools)
    config = {"configurable": {"thread_id": f"eval-{run_id}-{case.case_id}"}}
    state = {"ticket_id": case.case_id, "raw_message": case.raw_message, "customer_id": case.customer_id,
             "channel": case.channel, "received_at": anchor}

    started = time.perf_counter()
    error, approvals = None, []
    with tracing.ticket_trace(case.case_id) as run:
        state.update(run.ids())
        try:
            graph.invoke(state, config, context=ctx)
            while graph.get_state(config).next == ("await_approval",) and len(approvals) < MAX_APPROVAL_ROUNDS:
                paused = graph.get_state(config).values
                request = {"approval_id": paused["approval_id"], "tool_name": paused["plan"].tool_name,
                           "tool_args": {k: v for k, v in (paused["plan"].tool_args or {}).items() if k != "idempotency_key"}}
                yes = approver_says_yes(approver, case, request)
                approvals.append({**request, "decision": "approved" if yes else "denied"})
                decide_and_resume(graph, ctx.approval_store(), request["approval_id"], yes, APPROVER_ID, ctx)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            log.warning("Case %s crashed: %s", case.case_id, error, exc_info=log.isEnabledFor(logging.INFO))
            tracing.put(run.span, **{"deflect.error": error})
        final = graph.get_state(config).values
        run.finish(final)
    latency_ms = round((time.perf_counter() - started) * 1000)
    history = list(graph.get_state_history(config))[::-1]
    return outcome(case, final, ctx, latency_ms, error, approvals, checker_rounds(history), plan_rounds(history))


def reseed(anchor: str | None = None) -> None:
    """Resets the shop data. The anchor is the moment the data treats as now.

    v2, v3 and v4 each reseeded at the time of the run, so every prompt carried different dates
    and the same model gave different plans for a handful of tickets. A fixed anchor makes two
    runs see exactly the same tickets, so a change in the numbers comes from a change in the code.
    """
    from data.seed_orders import build_dataset, parse_anchor, write_to_db

    anchor = parse_anchor(anchor or os.getenv("DEFLECT_SEED_ANCHOR"))
    write_to_db(build_dataset(anchor), anchor, reset=True)
    print(f"Reseeded the database, anchor {anchor:%Y-%m-%d %H:%M} UTC")


def warn_about_stand_ins(records: list[dict]) -> None:
    """A run where the classifier or the checker dropped out part way is a mix of two
    configurations. It still finishes, so this says it where it cannot be missed."""
    classified = [r["case_id"] for r in records if r["predicted"].get("classifier_fell_back")]
    checked = [r["case_id"] for r in records if any(c.get("checker_fell_back") for c in r.get("checker_rounds") or [])]
    if not classified and not checked:
        return
    print(f"\nWARNING  The agent's own model stood in for the classifier on {len(classified)} tickets and for the "
          f"checker on {len(checked)}.", file=sys.stderr)
    print(f"  classifier: {' '.join(classified) or 'none'}\n  checker:    {' '.join(checked) or 'none'}", file=sys.stderr)
    print("  This run mixes two configurations. The gate fails it and the report will not record it. "
          "Find out why the service was unreachable, then run again.", file=sys.stderr)


def git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Deflect eval suite.")
    parser.add_argument("--subset", choices=["smoke", "full", "adversarial", "regression", "escalation"], default="smoke")
    parser.add_argument("--provider", choices=list(DEFAULT_MODELS), help="overrides DEFLECT_PROVIDER for this run")
    parser.add_argument("--model", help="overrides DEFLECT_MODEL for this run")
    parser.add_argument("--case", action="append", help="run only this case id, can be repeated")
    parser.add_argument("--workers", type=int, default=1, help="tickets run in parallel, keep 1 for Ollama")
    parser.add_argument("--out", type=Path, default=Path("results.json"))
    parser.add_argument("--reseed", action="store_true", help="reset and reseed the database before the run")
    parser.add_argument("--anchor", help="with --reseed, the time the data treats as now, for example "
                                         "2026-10-01T10:00:00+00:00. Defaults to DEFLECT_SEED_ANCHOR, then to the current time")
    parser.add_argument("--skip-verify", action="store_true", help="leave the checker out, to record the guardrails alone")
    parser.add_argument("--checker", help="overrides DEFLECT_CHECKER_PROVIDER for this run. agent means the agent's own model")
    parser.add_argument("--classifier", help="overrides DEFLECT_CLASSIFIER_PROVIDER for this run. agent means the agent's own model")
    parser.add_argument("--approver", choices=["label", "approve", "deny"], default="label",
                        help="how the simulated person answers approval requests")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    if args.provider:
        os.environ["DEFLECT_PROVIDER"] = args.provider
        # A model name from .env usually belongs to the old provider, so fall back to the default.
        os.environ["DEFLECT_MODEL"] = args.model or DEFAULT_MODELS[args.provider]
    elif args.model:
        os.environ["DEFLECT_MODEL"] = args.model
    if args.checker:
        os.environ["DEFLECT_CHECKER_PROVIDER"] = "" if args.checker == "agent" else args.checker
        os.environ["DEFLECT_CHECKER_MODEL"] = ""
    if args.classifier:
        os.environ["DEFLECT_CLASSIFIER_PROVIDER"] = "" if args.classifier == "agent" else args.classifier
        os.environ["DEFLECT_CLASSIFIER_MODEL"] = ""
    model = get_chat_model("agent")
    try:
        get_classifier(model)
        if not args.skip_verify:
            get_checker(model)
    except (ImportError, ValueError) as exc:
        print(f"Cannot start the classifier or the reply checker: {exc}", file=sys.stderr)
        return 2

    if args.reseed:
        reseed(args.anchor)
    with connect() as conn:
        anchor = fetch_seed_anchor(conn)
        writes = count_tool_requests(conn)
        missing = pending_migrations(conn)
    if anchor is None or writes is None or missing:
        print("The database is not ready for Phase 3. Run: python -m data.seed_orders --reset", file=sys.stderr)
        return 2
    if writes:
        print(f"The database already holds {writes} actions from an earlier run, so refunds and cancellations "
              "would not start from the labelled state.\nRun again with --reseed, or: python -m data.seed_orders --reset",
              file=sys.stderr)
        return 2
    ensure_indexed()

    try:
        tools = connect_tools(pin_clock=True)
        tools.start()
    except TransportError as exc:
        print(f"Could not start the MCP server: {exc}", file=sys.stderr)
        return 2

    cases = load_cases(args.subset, args.case)
    graph = build_graph(memory_checkpointer(), verify_replies=not args.skip_verify)
    run_id = uuid.uuid4().hex[:8]
    checker = "without the checker" if args.skip_verify else f"checked by {checker_name(model)}"
    try:
        backend = tracing.setup()
    except ValueError as exc:
        print(f"Tracing is off: {exc}", file=sys.stderr)
        backend = tracing.setup("none")
    print(f"Running {len(cases)} cases on {model.provider}:{model.name}, classified by {classifier_name(model)}, {checker}, "
          f"clock pinned to {anchor:%Y-%m-%d %H:%M} UTC, tracing to {backend}")

    records = []
    with tools, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for n, record in enumerate(pool.map(lambda c: run_case(graph, tools, c, anchor, run_id, args.approver), cases), start=1):
            records.append(record)
            p = record["predicted"]
            status = "ERROR" if record["error"] else f"{p['intent']} {p['decision']}"
            acted = "".join(f"{t['name']}{' refused' if t['error'] else ''}  " for t in record["tool_calls"])
            if record["approval"]["requested"]:
                acted += f"approval {record['approval']['decision']}  "
            if (record["guardrail"] or {}).get("outcome") == "deny":
                acted += f"denied by {record['guardrail']['check']}  "
            print(f"  [{n:>3}/{len(cases)}] {record['case_id']}  {status}  {acted}{record['latency_ms']} ms")
            if n == 3 and all(r["error"] for r in records):
                # Three crashes in a row is a setup problem, not an agent result.
                pool.shutdown(wait=False, cancel_futures=True)
                print(f"\nStopping, the first three cases all failed: {records[0]['error']}", file=sys.stderr)
                print("Check that Ollama is running with the model pulled, or that the API key is set.", file=sys.stderr)
                return 1

    metrics = compute(records)
    results = {
        "meta": {
            "run_id": run_id,
            "provider": model.provider,
            "model": model.name,
            "subset": args.subset if not args.case else "custom",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "seed_anchor": anchor.isoformat(),
            "mcp_transport": tools.label,
            "git_commit": git_commit(),
            "verify": not args.skip_verify,
            "checker": None if args.skip_verify else checker_name(model),
            "classifier": classifier_name(model),
            "trace_backend": backend,
            "approver": args.approver,
        },
        "metrics": metrics,
        "cases": records,
    }
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    tracing.flush()

    print()
    for key, value in metrics.items():
        if not isinstance(value, dict):
            print(f"  {key:<26} {value}")
    for tag, numbers in metrics["by_category"].items():
        print(f"  {tag:<26} " + ", ".join(f"{k} {v}" for k, v in numbers.items()))
    print(f"\nWrote {args.out}")
    warn_about_stand_ins(records)
    return 0


if __name__ == "__main__":
    sys.exit(main())
