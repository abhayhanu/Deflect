"""Builds what the console shows for one ticket: every step it took, in order.

Nothing here is stored. The steps are read back from the LangGraph checkpoints, one per node
that ran, and the tool calls and denials from the audit log. So the run view is the run, not
a description of it, and a ticket from last week opens the same way as one from a minute ago.

The state only ever holds placeholders for personal details, and every piece of text is put
through redaction once more before it leaves this module.
"""

import re
from datetime import datetime

from agent.guardrails.audit import AuditEntry
from agent.guardrails.policy import AUTO_APPROVE_REFUND_INR
from agent.guardrails.redact import PLACEHOLDER
from agent.nodes.act import clean_args
from agent.nodes.guardrail import FACTS, STRUCTURE
from api.tickets import placeholders_only

SECTION = re.compile(r"^## (.+)$", re.MULTILINE)
# Ids are shown whole. A long run of digits in one would otherwise be taken for a phone number.
NEVER_SCRUBBED = {"idempotency_key", "approval_id", "ticket_id", "trace_id", "at", "refund_id", "label_id", "escalation_id"}
ORDER_FIELDS = ("order_id", "status", "total_inr", "refunded_inr", "refundable_inr", "payment_method", "shipping_city",
                "belongs_to_customer", "hours_since_placed", "hours_since_delivered", "hours_past_promised_date")
CHECKS = [check.__name__ for check in STRUCTURE + FACTS]


def scrubbed(value, key: str | None = None):
    if key in NEVER_SCRUBBED:
        return value
    if isinstance(value, str):
        return placeholders_only(value)
    if isinstance(value, dict):
        return {k: scrubbed(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrubbed(v) for v in value]
    return value


def section(chunk: str) -> str:
    heading = SECTION.search(chunk)
    return heading.group(1).strip() if heading else "Overview"


def ladder(verdict) -> list[dict]:
    """Every guardrail check in the order it runs, and how far this action got.

    The first failure wins, so the checks after it never ran. A check that never ran is shown
    as not reached, which is different from passed.
    """
    stopped_at = CHECKS.index(verdict.check) if verdict.outcome == "deny" and verdict.check in CHECKS else None
    rungs = []
    for position, name in enumerate(CHECKS):
        if stopped_at is None or position < stopped_at:
            result = "passed"
        else:
            result = "blocked" if position == stopped_at else "not_reached"
        rungs.append({"check": name, "result": result})
    if stopped_at is None and verdict.outcome != "deny":
        asked = verdict.outcome == "approval" or verdict.authorized_by == "human"
        rungs.append({"check": "approval", "result": "waits_for_a_person" if verdict.outcome == "approval"
                      else "approved_by_a_person" if asked else "not_needed"})
    return rungs


def describe(node: str, values: dict) -> dict:
    """What one node decided, read from the state as it stood right after that node."""
    if node == "redact":
        return {"placeholders": sorted(set(PLACEHOLDER.findall(values.get("redacted_message") or "")))}
    if node == "classify":
        c = values["classification"]
        return {**c.model_dump(), "classified_by": values.get("classified_by"),
                "fell_back": bool(values.get("classifier_fell_back"))}
    if node == "retrieve":
        order = values.get("order")
        return {"policies": [{"doc_id": p.doc_id, "section": section(p.chunk), "score": round(p.score, 3), "pinned": p.pinned}
                             for p in values.get("policies") or []],
                "order": {k: order[k] for k in ORDER_FIELDS if k in order} if order else None}
    if node == "plan":
        plan = values["plan"]
        return {**plan.model_dump(), "tool_args": clean_args(plan.tool_args) or None, "loop_count": values.get("loop_count", 0)}
    if node == "guardrail":
        verdict, plan = values["guardrail"], values["plan"]
        return {"outcome": verdict.outcome, "check": verdict.check, "detail": verdict.detail,
                "authorized_by": verdict.authorized_by, "tool_name": plan.tool_name, "tool_args": clean_args(plan.tool_args),
                "ladder": ladder(verdict)}
    if node == "await_approval":
        decision = values.get("human_decision")
        return {"approved": decision.approved, "approver_id": decision.approver_id, "note": decision.note} if decision else {}
    if node == "act":
        call = values["tool_calls"][-1]
        return call.model_dump(mode="json", exclude={"called_at"})
    if node == "draft":
        return {"draft": values.get("draft")}
    if node == "verify":
        check = values["verification"]
        return {**check.model_dump(include={"verdict", "failed_checks", "unsupported_claims", "checked_by", "checker_fell_back"}),
                "retry_count": values.get("retry_count", 0)}
    if node == "escalate":
        handed = values.get("escalation") or {}
        case = handed.get("case") or {}
        return {"terminal_reason": values.get("terminal_reason"), "queue": handed.get("reason"),
                "signals": handed.get("signals") or [], "escalation_id": case.get("escalation_id"),
                "priority": case.get("priority"), "respond_within_hours": case.get("respond_within_hours"),
                "error": handed.get("error")}
    if node == "respond":
        return {"terminal_reason": values.get("terminal_reason")}
    return {}


def took_ms(before, after) -> int | None:
    try:
        gap = datetime.fromisoformat(after.created_at) - datetime.fromisoformat(before.created_at)
    except (TypeError, ValueError):
        return None
    return max(0, round(gap.total_seconds() * 1000))


def steps(history: list) -> list[dict]:
    """One entry per node that ran, oldest first. history is every checkpoint of the ticket,
    oldest first, and the node that turned one checkpoint into the next is the one the earlier
    checkpoint said would run."""
    out = []
    for before, after in zip(history, history[1:]):
        node = before.next[0] if before.next else None
        if node is None or node.startswith("__"):
            continue
        out.append({"node": node, "at": after.created_at, "took_ms": took_ms(before, after),
                    "detail": scrubbed(describe(node, after.values))})
    return out


def audit_rows(entries: list[AuditEntry]) -> list[dict]:
    return [scrubbed({"tool_name": e.tool_name, "tool_args": e.tool_args, "result": e.result, "error": e.error,
                      "authorized_by": e.authorized_by, "approver_id": e.approver_id, "check_name": e.check_name,
                      "latency_ms": e.latency_ms, "at": e.created_at.isoformat()}) for e in entries]


def limits() -> dict:
    return {"checks": CHECKS, "auto_approve_refund_inr": AUTO_APPROVE_REFUND_INR}
