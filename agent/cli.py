"""Runs one ticket through the graph from the terminal, or resumes one that was interrupted.

Every step is checkpointed in Postgres, so a run killed half way can be continued with the
resume option and the thread id it printed. The crash option kills the process on purpose
after a chosen node, which is how the resume behaviour is tested.

A refund above the auto approve ceiling pauses the run and prints an approval id. The process
then ends. Approve or deny it later, from a new process, with the approve or deny option and
your name, and the run continues from its checkpoint.

Tools come from the MCP server. It is started over stdio, or reached over HTTP when
MCP_TRANSPORT is http.
"""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from langgraph.types import Command

from agent import tracing
from agent.approvals import ApprovalError, PostgresApprovals, decide_and_resume
from agent.context import RunContext
from agent.graph import NODES, build_graph, postgres_checkpointer
from agent.mcp_client import TransportError
from agent.mcp_client import connect as connect_tools
from data.db import connect
from data.index_policies import ensure_indexed
from data.queries import fetch_seed_anchor

GOLDEN_FILE = Path(__file__).resolve().parent.parent / "evals" / "golden" / "tickets.jsonl"


def load_case(case_id: str) -> dict:
    for line in GOLDEN_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line)["case_id"] == case_id:
            return json.loads(line)
    raise SystemExit(f"No golden case called {case_id}")


def summary(node: str, update: dict) -> str:
    if node == "classify":
        c = update["classification"]
        by = f" by {update['classified_by']}{' as a fallback' if update.get('classifier_fell_back') else ''}"
        return f"intent={c.intent} confidence={c.confidence:.2f} order_id={c.order_id}{by}"
    if node == "retrieve":
        ids = sorted({p.doc_id for p in update["policies"]})
        return f"policies={ids} order_found={update['order'] is not None}"
    if node == "plan":
        p = update["plan"]
        tool = f" tool={p.tool_name} args={json.dumps(p.tool_args)}" if p.tool_name else ""
        return f"decision={p.decision} cites={p.cites}{tool} loop_count={update['loop_count']}"
    if node == "guardrail":
        g = update["guardrail"]
        return g.outcome if g.outcome == "allow" else f"{g.outcome} by {g.check}: {g.detail}"
    if node == "await_approval":
        d = update["human_decision"]
        return f"{'approved' if d.approved else 'denied'} by {d.approver_id}"
    if node == "act":
        call = update["tool_calls"][-1]
        outcome = f"refused: {call.error}" if call.error else f"ok {json.dumps(call.result)}"
        return f"{call.name} {outcome} in {call.latency_ms} ms, authorized by {call.authorized_by}"
    if node == "draft":
        return f"{len(update['draft'].split())} words"
    if node == "verify":
        v = update["verification"]
        claims = f" {v.unsupported_claims}" if v.unsupported_claims else ""
        by = f" checked by {v.checked_by}{' as a fallback' if v.checker_fell_back else ''}" if v.checked_by else ""
        return f"{v.verdict} retry_count={update['retry_count']}{by}{claims}"
    if node == "escalate":
        case = (update["escalation"] or {}).get("case") or {}
        return f"{update['terminal_reason']} case={case.get('escalation_id')}"
    if node == "respond":
        return f"terminal_reason={update['terminal_reason']}"
    return ", ".join(sorted(update))


def print_pause(values: dict) -> None:
    print(f"\nWaiting for a person to approve {values['approval_id']}: {values['guardrail'].detail}")
    print(f"  Approve: python -m agent.cli --approve {values['approval_id']} --approver YOUR_NAME")
    print(f"  Deny:    python -m agent.cli --deny {values['approval_id']} --approver YOUR_NAME --note REASON")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one support ticket through Deflect.")
    parser.add_argument("message", nargs="?", help="the ticket text")
    parser.add_argument("--customer", help="customer id, for example C_1182")
    parser.add_argument("--channel", default="email", choices=["email", "chat", "web_form"])
    parser.add_argument("--case", help="run a golden case by its id, for example gold_011")
    parser.add_argument("--thread", help="thread id for the checkpoint, a new one by default")
    parser.add_argument("--resume", metavar="THREAD", help="continue an interrupted run")
    parser.add_argument("--crash-after", choices=list(NODES), help="kill the process after this node")
    parser.add_argument("--real-clock", action="store_true", help="use the real time instead of the seed anchor")
    parser.add_argument("--skip-verify", action="store_true", help="leave the reply checker out")
    parser.add_argument("--approve", metavar="APPROVAL_ID", help="approve a paused action and continue the run")
    parser.add_argument("--deny", metavar="APPROVAL_ID", help="deny a paused action and continue the run")
    parser.add_argument("--approver", help="your name, recorded with the decision")
    parser.add_argument("--note", help="a reason for the decision")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if (args.approve or args.deny) and not args.approver:
        parser.error("say who is deciding with --approver")

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    with connect() as conn:
        anchor = fetch_seed_anchor(conn)
    try:
        # Also loads the embedding model once here, where nothing is waiting on a timeout.
        ensure_indexed()
    except Exception as exc:
        print(f"Policy search is not ready: {str(exc)[:300]}", file=sys.stderr)
        print("Is Qdrant running? Try: docker compose up -d", file=sys.stderr)
        return 1
    try:
        RunContext().classifier()
        if not args.skip_verify:
            RunContext().checker()
    except (ImportError, ValueError) as exc:
        print(f"Cannot start the classifier or the reply checker: {exc}", file=sys.stderr)
        return 1
    tools = connect_tools(pin_clock=not args.real_clock)
    try:
        tools.start()
    except TransportError as exc:
        print(f"Could not reach the MCP server: {exc}", file=sys.stderr)
        return 1
    ctx = RunContext(now=None if args.real_clock else anchor, tools=tools)

    with postgres_checkpointer() as saver, tools:
        graph = build_graph(saver, verify_replies=not args.skip_verify)

        if args.approve or args.deny:
            approval_id = args.approve or args.deny
            try:
                final = decide_and_resume(graph, PostgresApprovals(), approval_id, bool(args.approve), args.approver,
                                          ctx, note=args.note)
            except ApprovalError as exc:
                print(exc)
                return 1
            tracing.flush()
            print(f"{'Approved' if args.approve else 'Denied'} {approval_id}, the run continued to "
                  f"{final.get('terminal_reason')}.")
            print(f"\n{final['reply']}")
            return 0

        thread = args.resume or args.thread or f"cli-{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread}}

        if args.resume:
            snapshot = graph.get_state(config)
            if not snapshot.values:
                print(f"No run found for thread {thread}")
                return 1
            if not snapshot.next:
                print("That run already finished.\n")
                print(snapshot.values.get("reply", ""))
                return 0
            state = None
            if snapshot.next == ("await_approval",):
                request = PostgresApprovals().get(snapshot.values["approval_id"])
                if request is None or request.status == "pending":
                    print_pause(snapshot.values)
                    return 0
                # The decision was recorded but the run stopped before it arrived, so deliver it now.
                state = Command(resume=request.resume_value())
            print(f"Resuming thread {thread} at {snapshot.next[0]}")
        else:
            if args.case:
                case = load_case(args.case)
                message, customer, channel = case["raw_message"], case["customer_id"], case["channel"]
            else:
                if not args.message:
                    parser.error("give a message, a golden case or a thread to resume")
                message, customer, channel = args.message, args.customer, args.channel
            state = {"ticket_id": args.case or thread, "raw_message": message, "customer_id": customer,
                     "channel": channel, "received_at": ctx.clock()}
            print(f"Thread {thread}")

        earlier = graph.get_state(config).values
        ticket_id = earlier.get("ticket_id") or state["ticket_id"]
        with tracing.ticket_trace(ticket_id, earlier.get("trace_parent"), resumed=bool(args.resume)) as run:
            if isinstance(state, dict):
                state.update(run.ids())
            try:
                for update in graph.stream(state, config, context=ctx, stream_mode="updates", durability="sync"):
                    for node, values in update.items():
                        if node == "__interrupt__":
                            continue
                        print(f"  {node:<14} {summary(node, values)}")
                        if node == args.crash_after:
                            print(f"\nKilled after {node}. Resume with: python -m agent.cli --resume {thread}")
                            sys.stdout.flush()
                            tracing.flush()
                            os._exit(1)
            except TransportError as exc:
                tracing.flush()
                print(f"\nThe MCP server did not answer: {exc}", file=sys.stderr)
                print(f"Nothing was lost. Continue with: python -m agent.cli --resume {thread}", file=sys.stderr)
                return 1
            snapshot = graph.get_state(config)
            run.finish(snapshot.values)
        tracing.flush()
    if snapshot.next == ("await_approval",):
        print_pause(snapshot.values)
        return 0
    print(f"\n{snapshot.values['reply']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
