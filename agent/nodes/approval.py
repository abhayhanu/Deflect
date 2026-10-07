"""Pauses the run until a person approves or denies the action.

interrupt saves the run in the checkpointer and stops. Nothing is held in memory while it
waits, so the process can be killed and restarted before anyone answers. When the answer
arrives, LangGraph runs this node again from the top and interrupt returns the answer instead
of stopping. That is why this node does nothing before interrupt except read the state.
"""

import logging
from datetime import datetime, timezone

from langgraph.runtime import Runtime
from langgraph.types import interrupt

from agent.context import RunContext
from agent.guardrails.audit import AuditEntry
from agent.nodes.act import clean_args
from agent.state import HumanDecision, TicketState

log = logging.getLogger("agent.nodes.approval")


def await_approval(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    plan = state["plan"]
    args = clean_args(plan.tool_args)
    answer = interrupt({
        "approval_id": state["approval_id"],
        "ticket_id": state["ticket_id"],
        "tool_name": plan.tool_name,
        "tool_args": args,
        "reason": state["guardrail"].detail,
    })

    if not isinstance(answer, dict) or answer.get("approval_id") != state["approval_id"]:
        raise ValueError(f"the run was resumed with an answer for a different approval: {answer!r}")
    decision = HumanDecision(
        approval_id=state["approval_id"],
        approved=bool(answer.get("approved")),
        approver_id=str(answer.get("approver_id") or "unknown"),
        note=answer.get("note"),
        decided_at=datetime.now(timezone.utc),
    )
    update = {"awaiting_approval": False, "human_decision": decision}
    if decision.approved:
        log.info("Approval %s granted by %s", decision.approval_id, decision.approver_id)
        return update

    log.warning("Approval %s denied by %s", decision.approval_id, decision.approver_id)
    runtime.context.audit_log().record(AuditEntry(
        ticket_id=state["ticket_id"], trace_id=state.get("trace_id") or state["ticket_id"], tool_name=plan.tool_name,
        tool_args=args, authorized_by="denied", approver_id=decision.approver_id, check_name="human_denied",
        error=decision.note or "denied by a person",
    ))
    return {**update, "terminal_reason": "escalated_human_denied"}
