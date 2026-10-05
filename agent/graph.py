"""Wires the nodes into a LangGraph state machine.

Phase 3 flow:

    redact, classify, then escalate if the ticket is out of scope or unclear
    otherwise retrieve, plan, and then
        answer    draft, verify
        act       guardrail, which allows, pauses for a human, or denies
                      allow     act, which runs one tool through MCP, then draft and verify
                      approval  await_approval, then back to guardrail on fresh data, or escalate
                      deny      escalate
        escalate  escalate
    verify passes to respond, retries at most twice, or escalates
    escalate opens a case in the support queue, then respond

Without the checker, used to record the guardrails on their own, draft goes straight to respond.
"""

import logging
import time
from contextlib import contextmanager

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from agent import tracing
from agent.context import RunContext
from agent.guardrails.policy import classification_escalation, plan_outcome
from agent.nodes.act import act
from agent.nodes.approval import await_approval
from agent.nodes.classify import classify
from agent.nodes.draft import draft
from agent.nodes.escalate import escalate
from agent.nodes.guardrail import guardrail
from agent.nodes.plan import plan
from agent.nodes.redact import redact
from agent.nodes.respond import respond
from agent.nodes.retrieve import retrieve
from agent.nodes.verify import verify
from agent.state import (
    Classification,
    GuardrailVerdict,
    HumanDecision,
    Plan,
    RetrievedPolicy,
    TicketState,
    ToolCall,
    Verification,
)

log = logging.getLogger("agent.graph")

NODES = {
    "redact": redact, "classify": classify, "retrieve": retrieve, "plan": plan, "guardrail": guardrail,
    "await_approval": await_approval, "act": act, "draft": draft, "verify": verify, "escalate": escalate,
    "respond": respond,
}


def logged(name: str, node):
    """Logs each node and gives it its own span in the ticket's trace."""
    kind = "guardrail" if name == "guardrail" else "node"

    def run(state: TicketState, runtime: Runtime[RunContext]) -> dict:
        log.info("enter %s ticket=%s", name, state.get("ticket_id"))
        started = time.perf_counter()
        with tracing.span(name, kind, **{"deflect.node": name, "deflect.ticket_id": state.get("ticket_id")}) as span:
            update = node(state, runtime)
            tracing.put(span, **tracing.node_attributes(name, state, update))
        log.info("exit %s in %.0f ms, wrote %s", name, (time.perf_counter() - started) * 1000, sorted(update))
        return update

    run.__name__ = name
    return run


def route_after_classify(state: TicketState) -> str:
    return "escalate" if classification_escalation(state.get("classification")) else "retrieve"


def route_after_plan(state: TicketState) -> str:
    return {"answer": "draft", "act": "guardrail", "escalate": "escalate"}[plan_outcome(state)]


def route_after_guardrail(state: TicketState) -> str:
    return {"allow": "act", "approval": "await_approval", "deny": "escalate"}[state["guardrail"].outcome]


def route_after_approval(state: TicketState) -> str:
    decision = state.get("human_decision")
    return "guardrail" if decision and decision.approved else "escalate"


def route_after_act(state: TicketState) -> str:
    return "escalate" if state["tool_calls"][-1].error else "draft"


def route_after_verify(state: TicketState) -> str:
    verdict = state["verification"].verdict
    if verdict == "pass":
        return "respond"
    if verdict == "escalate":
        return "escalate"
    # Once an action has run, a retry only rewrites the reply. Planning again could act twice.
    acted = any(not call.error for call in state.get("tool_calls") or [])
    return "draft" if acted else "plan"


def build_graph(checkpointer=None, verify_replies: bool = True):
    builder = StateGraph(TicketState, context_schema=RunContext)
    for name, node in NODES.items():
        if name == "verify" and not verify_replies:
            continue
        builder.add_node(name, logged(name, node))

    builder.add_edge(START, "redact")
    builder.add_edge("redact", "classify")
    builder.add_conditional_edges("classify", route_after_classify, ["retrieve", "escalate"])
    builder.add_edge("retrieve", "plan")
    builder.add_conditional_edges("plan", route_after_plan, ["draft", "guardrail", "escalate"])
    builder.add_conditional_edges("guardrail", route_after_guardrail, ["act", "await_approval", "escalate"])
    builder.add_conditional_edges("await_approval", route_after_approval, ["guardrail", "escalate"])
    builder.add_conditional_edges("act", route_after_act, ["draft", "escalate"])
    if verify_replies:
        builder.add_edge("draft", "verify")
        builder.add_conditional_edges("verify", route_after_verify, ["respond", "draft", "plan", "escalate"])
    else:
        builder.add_edge("draft", "respond")
    builder.add_edge("escalate", "respond")
    builder.add_edge("respond", END)
    return builder.compile(checkpointer=checkpointer)


def checkpoint_serde():
    # Only our own state models may be rebuilt from a checkpoint, nothing else.
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    models = [Classification, RetrievedPolicy, Plan, ToolCall, Verification, GuardrailVerdict, HumanDecision]
    return JsonPlusSerializer(allowed_msgpack_modules=[(m.__module__, m.__name__) for m in models])


def memory_checkpointer():
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver(serde=checkpoint_serde())


@contextmanager
def postgres_checkpointer():
    """A checkpointer that saves state after every node, so a killed run can resume.

    It connects as the agent's own role. The owner created the tables when the database was
    migrated, so this never needs to create anything.
    """
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    from data.db import app_database_url

    with Connection.connect(app_database_url(), autocommit=True, prepare_threshold=0, row_factory=dict_row) as conn:
        yield PostgresSaver(conn, serde=checkpoint_serde())
