"""Decides whether the one action the plan chose may run. Pure Python, no model call.

The checks run in a fixed order and the first failure wins:

    tool_exists       the server really has this tool
    allowlist         the tool is allowed for the ticket's intent
    arguments         the arguments match the tool's schema, with nothing extra
    escalation_rules  the message is not one that pol_escalation sends to a person untouched
    order_scope       the action targets the order named in the ticket, on the customer's own account
    tool_budget       fewer than four actions so far on this ticket
    cost_cap          the ticket has not spent its model budget
    hard_ceiling      no refund above Rs 25,000, ever
    policy_rules      the numeric rules in the policy documents hold for this order
    duplicate_refund  no refund on this order in the last 24 hours

Approval comes last, so a person is never asked to approve something the rules would refuse
anyway. A refund above Rs 3,000 that passes everything pauses for a human. After the human
approves, this node runs again on freshly read data, because the order may have changed while
the request waited.

Every check reads the tool the plan chose, before anything runs. Checking the tool that ran
would be an incident report, not a guardrail.
"""

import copy
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from jsonschema import Draft202012Validator
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.approvals import ApprovalRequest, approval_id_for
from agent.context import AuditedTools, RunContext
from agent.guardrails.audit import AuditEntry
from agent.guardrails.policy import (
    AUTO_APPROVE_REFUND_INR,
    COST_CAP_INR,
    DUPLICATE_REFUND_WINDOW,
    HARD_REFUND_CEILING_INR,
    MAX_TOOL_CALLS,
    MONEY_TOOLS,
    TOOL_ALLOWLIST,
    broken_rules,
    escalation_signals,
)
from agent.mcp_client import ToolSpec
from agent.nodes.act import clean_args, idempotency_key
from agent.nodes.retrieve import history_facts, order_facts
from agent.state import GuardrailVerdict, TicketState

log = logging.getLogger("agent.nodes.guardrail")


@dataclass
class Proposal:
    tool: str
    args: dict
    intent: str
    specs: list[ToolSpec]
    ticket_order_id: str | None = None
    customer_id: str | None = None
    order: dict | None = None
    history: dict | None = None
    calls_so_far: int = 0
    cost_inr: float = 0.0
    message: str = ""

    def spec(self) -> ToolSpec | None:
        return next((s for s in self.specs if s.name == self.tool), None)

    def amount(self) -> float:
        return float(self.args.get("amount_inr") or 0) if self.tool == "issue_refund" else 0.0


Check = Callable[[Proposal], str | None]


def tool_exists(p: Proposal) -> str | None:
    return None if p.spec() else f"the MCP server has no tool called {p.tool}"


def allowlist(p: Proposal) -> str | None:
    return None if p.tool in TOOL_ALLOWLIST.get(p.intent, set()) else f"{p.tool} is not allowed for a {p.intent} ticket"


def strict_schema(spec: ToolSpec) -> dict:
    """The tool's own schema, without the idempotency key the act node adds, and with unknown
    arguments refused. A model that invents an argument is guessing, and a guess is not sent."""
    schema = copy.deepcopy(spec.input_schema)
    schema.get("properties", {}).pop("idempotency_key", None)
    schema["required"] = [r for r in schema.get("required", []) if r != "idempotency_key"]
    schema["additionalProperties"] = False
    return schema


def arguments(p: Proposal) -> str | None:
    errors = sorted(Draft202012Validator(strict_schema(p.spec())).iter_errors(p.args), key=lambda e: list(e.path))
    if not errors:
        return None
    return "; ".join(f"{'.'.join(map(str, e.path)) or 'arguments'}: {e.message}" for e in errors[:3])


def escalation_rules(p: Proposal) -> str | None:
    """Who is asking, and how, can rule an action out even when the order itself allows it."""
    if p.spec().read_only:
        return None
    found = escalation_signals(p.message)
    return "; ".join(s.cited() for s in found) or None


def order_scope(p: Proposal) -> str | None:
    if "order_id" not in p.spec().input_schema.get("properties", {}):
        return None
    target = p.args.get("order_id")
    if p.ticket_order_id is None:
        return f"the ticket names no order, so an action on {target} has nothing backing it"
    if target != p.ticket_order_id:
        return f"the action targets {target} but the ticket is about {p.ticket_order_id}"
    if p.order is None:
        return f"order {target} does not exist"
    if not p.order.get("belongs_to_customer", False):
        return f"order {target} is on another customer's account"
    return None


def tool_budget(p: Proposal) -> str | None:
    return None if p.calls_so_far < MAX_TOOL_CALLS else f"the ticket already made {p.calls_so_far} actions"


def cost_cap(p: Proposal) -> str | None:
    return None if p.cost_inr < COST_CAP_INR else f"the ticket has spent Rs {p.cost_inr:.2f}, the cap is Rs {COST_CAP_INR:.2f}"


def hard_ceiling(p: Proposal) -> str | None:
    if p.amount() > HARD_REFUND_CEILING_INR:
        return f"Rs {p.amount():,.0f} is above the Rs {HARD_REFUND_CEILING_INR:,} ceiling"
    return None


def policy_rules(p: Proposal) -> str | None:
    if p.order is None:
        return None
    broken = broken_rules(p.tool, p.args, p.order, p.history)
    return "; ".join(f"{r.says} ({r.policy})" for r in broken) or None


def duplicate_refund(p: Proposal) -> str | None:
    if p.tool not in MONEY_TOOLS or p.order is None:
        return None
    window_days = DUPLICATE_REFUND_WINDOW.total_seconds() / 86400
    recent = [r for r in p.order.get("refunds", []) if (r.get("days_ago") or 0) < window_days]
    return f"order {p.order['order_id']} already had refund {recent[0]['refund_id']} in the last 24 hours" if recent else None


STRUCTURE: list[Check] = [tool_exists, allowlist, arguments, escalation_rules]
FACTS: list[Check] = [order_scope, tool_budget, cost_cap, hard_ceiling, policy_rules, duplicate_refund]


def first_failure(checks: list[Check], p: Proposal) -> GuardrailVerdict | None:
    for check in checks:
        detail = check(p)
        if detail:
            return GuardrailVerdict(outcome="deny", check=check.__name__, detail=detail)
    return None


def evaluate(p: Proposal, approved_by_human: bool = False) -> GuardrailVerdict:
    denied = first_failure(STRUCTURE + FACTS, p)
    if denied:
        return denied
    if p.amount() > AUTO_APPROVE_REFUND_INR and not approved_by_human:
        return GuardrailVerdict(outcome="approval", check="approval",
                                detail=f"a Rs {p.amount():,.0f} refund is above the Rs {AUTO_APPROVE_REFUND_INR:,} auto approve ceiling")
    return GuardrailVerdict(outcome="allow", authorized_by="human" if approved_by_human else "policy")


def read_facts(tools: AuditedTools, p: Proposal, now: datetime) -> None:
    """Reads the order and history again at decision time. The copies in the state are from
    retrieve, which can be hours old once a run has waited for a human."""
    if p.args.get("order_id"):
        found = tools.call("get_order", {"order_id": p.args["order_id"]})
        if found.ok and found.result:
            p.order = order_facts(found.result, p.customer_id, now)
    if p.customer_id:
        history = tools.call("get_customer_history", {"customer_id": p.customer_id})
        if history.ok and history.result:
            p.history = history_facts(history.result, now)


def guardrail(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    plan = state["plan"]
    args = clean_args(plan.tool_args)
    key = idempotency_key(state["ticket_id"], plan.tool_name, args)
    tools = ctx.tools_for(state)
    proposal = Proposal(
        tool=plan.tool_name, args=args, intent=state["classification"].intent, specs=tools.tools(),
        ticket_order_id=state["classification"].order_id, customer_id=state.get("customer_id"),
        calls_so_far=len(state.get("tool_calls") or []), cost_inr=state.get("cost_inr", 0.0),
        # The raw message, so nothing is hidden behind a placeholder. It is only matched here and
        # never copied into the verdict, the audit log or a trace.
        message=state.get("raw_message") or "",
    )

    verdict = first_failure(STRUCTURE, proposal)
    if verdict is None:
        read_facts(tools, proposal, ctx.clock())
        decision = state.get("human_decision")
        approved = bool(decision and decision.approved and decision.approval_id == approval_id_for(key))
        verdict = evaluate(proposal, approved_by_human=approved)
    verdict = verdict.model_copy(update={"action_key": key})

    if verdict.outcome == "deny":
        log.warning("Guardrail %s denied %s on %s: %s", verdict.check, plan.tool_name, state["ticket_id"], verdict.detail)
        ctx.audit_log().record(AuditEntry(
            ticket_id=state["ticket_id"], trace_id=state.get("trace_id") or state["ticket_id"], tool_name=plan.tool_name,
            tool_args=args, authorized_by="denied", check_name=verdict.check, error=verdict.detail,
        ))
        return {"guardrail": verdict, "terminal_reason": f"escalated_guardrail_{verdict.check}"}

    if verdict.outcome == "approval":
        approval_id = approval_id_for(key)
        thread_id = get_config()["configurable"]["thread_id"]
        ctx.approval_store().request(ApprovalRequest(
            approval_id=approval_id, ticket_id=state["ticket_id"], thread_id=thread_id,
            tool_name=plan.tool_name, tool_args=args, reason=verdict.detail,
        ))
        log.info("Approval %s requested for %s on %s", approval_id, plan.tool_name, state["ticket_id"])
        return {"guardrail": verdict, "awaiting_approval": True, "approval_id": approval_id}

    return {"guardrail": verdict}
