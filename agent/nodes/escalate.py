"""Hands the ticket to a person by opening a case in the support queue through MCP.

This is the only write the agent makes without the plan choosing it, and it cannot move money.
The case summary is built from the classification and the reason, never from the customer's
own words, so no personal details reach the queue. If the queue cannot be reached, the ticket
still gets its reply and the failure is kept in the state.
"""

import logging
import re

from langgraph.runtime import Runtime

from agent.context import RunContext
from agent.guardrails.policy import MAX_LOOPS, SIGNAL_QUEUE, early_escalation, escalation_signals, plan_outcome
from agent.mcp_client import ToolOutcome, TransportError
from agent.nodes.act import idempotency_key, replayed
from agent.state import TicketState

log = logging.getLogger("agent.nodes.escalate")

# The queue's reason codes, found from the words the plan used. The first match wins.
QUEUE_REASONS = [
    ("safety", r"safety|injur|fire|smoke|spark|burn|shock|battery|allerg"),
    ("legal", r"legal|lawyer|court|forum|police|regulator|helpline"),
    ("fraud_or_account", r"fraud|hack|did not place|unrecogni|account (security|access)|lost access"),
    ("someone_elses_order", r"someone else|another customer|not (on|in) the customer|belong"),
    ("instructions_to_assistant", r"instruction|claims? to be|staff|admin|approved|authori[sz]|inject|ignore"),
    ("human_requested", r"human|manager|supervisor|call ?back|speak to"),
    ("compensation", r"compensat|voucher|goodwill|discount|price difference"),
    ("conduct", r"rude|conduct|harass|behaviou?r|extra money"),
    ("payment_dispute", r"chargeback|double charge|charged twice|debited|payment dispute"),
    ("privacy", r"privacy|delete (my|the) (account|data)|personal data|copy of (my|the) data"),
    ("not_covered", r"not covered|no policy|out of scope|general knowledge"),
]
URGENT = {"safety", "legal"}
KNOWN_PLAN_REASONS = ("action_required", "parse_failure", "act_without_tool")


def terminal_reason(state: TicketState) -> str:
    """Why this ticket ended with a person. A node that stopped the run, such as the guardrail or
    the checker, already wrote its own reason and that one is kept."""
    if state.get("terminal_reason"):
        return state["terminal_reason"]
    early = early_escalation(state)
    if early:
        return f"escalated_{early}"
    calls = state.get("tool_calls") or []
    if plan_outcome(state) == "act" and calls and calls[-1].error:
        return "escalated_tool_error"
    if state.get("loop_count", 0) > MAX_LOOPS:
        return "escalated_loop_cap"
    reason = state["plan"].escalation_reason if state.get("plan") else None
    return f"escalated_{reason}" if reason in KNOWN_PLAN_REASONS else "escalated_by_plan"


def signals(state: TicketState, terminal: str) -> list:
    """The escalation rules the message matched, when that is what stopped the ticket."""
    return escalation_signals(state.get("raw_message")) if terminal == "escalated_message_signal" else []


def queue_reason(state: TicketState, terminal: str) -> str:
    if terminal == "escalated_out_of_scope":
        return "not_covered"
    matched = signals(state, terminal)
    if matched:
        return SIGNAL_QUEUE[min(s.rule for s in matched)]
    if terminal == "escalated_guardrail_order_scope" and "another customer" in (state["guardrail"].detail or ""):
        return "someone_elses_order"
    plan = state.get("plan")
    words = " ".join(filter(None, [plan.escalation_reason if plan else None, plan.rationale if plan else None])).lower()
    for reason, pattern in QUEUE_REASONS:
        if re.search(pattern, words):
            return reason
    return "needs_review"


def case_summary(state: TicketState, terminal: str) -> str:
    c = state.get("classification")
    parts = [f"{c.intent if c else 'unclassified'} ticket, {terminal.removeprefix('escalated_').replace('_', ' ')}."]
    # Only the rule that matched is named. The customer's own words never go to the queue.
    parts += [f"{s.cited().capitalize()}." for s in signals(state, terminal)]
    plan = state.get("plan")
    if plan and plan.escalation_reason:
        parts.append(f"Plan said: {plan.escalation_reason}.")
    guard = state.get("guardrail")
    if guard and guard.outcome == "deny":
        parts.append(f"Guardrail {guard.check}: {guard.detail}.")
    done = [call for call in state.get("tool_calls") or [] if not call.error]
    if done:
        parts.append(f"Already done: {done[-1].name} {done[-1].result}.")
    return " ".join(parts)[:1000]


def escalate(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    terminal = terminal_reason(state)
    reason = queue_reason(state, terminal)
    c = state.get("classification")
    order = state.get("order")
    args = {
        "ticket_id": state["ticket_id"],
        "reason": reason,
        "summary": case_summary(state, terminal),
        "priority": "urgent" if reason in URGENT or (c and c.urgency == "high") else "normal",
    }
    if order and "status" in order:
        args["order_id"] = order["order_id"]

    key = idempotency_key(state["ticket_id"], "escalate_to_human", args)
    try:
        outcome = ctx.tools_for(state).call("escalate_to_human", {**args, "idempotency_key": key})
    except TransportError as exc:
        outcome = ToolOutcome(error=f"transport_error: {exc}")
    if (outcome.error or "").startswith("duplicate_request"):
        outcome = replayed(outcome)
    if outcome.error:
        log.warning("Could not open a case for %s: %s", state["ticket_id"], outcome.error)

    return {"terminal_reason": terminal, "escalation": {"case": outcome.result, "error": outcome.error, "reason": reason,
                                                        "signals": [s.cited() for s in signals(state, terminal)]}}
