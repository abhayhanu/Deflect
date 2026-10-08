import hashlib
import json
import logging
import time
from datetime import datetime, timezone

from langgraph.runtime import Runtime

from agent.context import RunContext
from agent.guardrails.redact import rehydrate
from agent.mcp_client import ToolOutcome, TransportError
from agent.state import TicketState, ToolCall

log = logging.getLogger("agent.nodes.act")

EARLIER_RESULT = "Earlier result: "


class GuardrailBypass(RuntimeError):
    pass


def clean_args(args: dict | None) -> dict:
    """The model never chooses the idempotency key. If it tried, its value is thrown away."""
    return {k: v for k, v in (args or {}).items() if k != "idempotency_key"}


def idempotency_key(ticket_id: str, tool_name: str, args: dict) -> str:
    """Same ticket, same tool, same arguments, same key. A resumed or retried run can never
    make the server do the same thing twice."""
    digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{ticket_id}:{tool_name}:{digest}"


def with_real_values(args: dict, mapping: dict[str, str]) -> dict:
    return {k: rehydrate(v, mapping) if isinstance(v, str) else v for k, v in args.items()}


def replayed(outcome: ToolOutcome) -> ToolOutcome:
    """A duplicate_request answer means this exact action on this exact ticket already ran,
    either before a lost connection or before the process died. So it counts as done, with the
    result the server kept from the first time."""
    text = outcome.error or ""
    earlier = None
    if EARLIER_RESULT in text:
        try:
            earlier = json.loads(text.split(EARLIER_RESULT, 1)[1])
        except ValueError:
            earlier = None
    result = earlier if isinstance(earlier, dict) else {"status": "already_done", "detail": text}
    log.info("The server had already run this action, recording it as done")
    return ToolOutcome(result=result, attempts=outcome.attempts, latency_ms=outcome.latency_ms)


def act(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    """Runs exactly one tool, the one the plan chose, and only with an allow verdict from the
    guardrail for exactly these arguments."""
    ctx = runtime.context
    plan = state["plan"]
    args = clean_args(plan.tool_args)
    key = idempotency_key(state["ticket_id"], plan.tool_name, args)

    verdict = state.get("guardrail")
    if verdict is None or verdict.outcome != "allow" or verdict.action_key != key:
        raise GuardrailBypass(f"act reached {plan.tool_name} without an allow verdict for this exact action")
    decision = state.get("human_decision")
    approver = decision.approver_id if verdict.authorized_by == "human" and decision else None

    # The model and the saved state only ever see placeholders. The real address goes to the server.
    live_args = {**with_real_values(args, ctx.pii_map(state["raw_message"])), "idempotency_key": key}
    logged_args = {**args, "idempotency_key": key}
    started = time.perf_counter()
    try:
        outcome = ctx.tools_for(state).call(plan.tool_name, live_args, authorized_by=verdict.authorized_by,
                                            approver_id=approver, logged_args=logged_args)
    except TransportError as exc:
        outcome = ToolOutcome(error=f"transport_error: {exc}", attempts=2)

    if (outcome.error or "").startswith("duplicate_request"):
        outcome = replayed(outcome)

    result = outcome.result if isinstance(outcome.result, dict) or outcome.result is None else {"value": outcome.result}
    call = ToolCall(
        name=plan.tool_name,
        args=logged_args,
        result=result,
        error=outcome.error,
        authorized_by=verdict.authorized_by,
        approver_id=approver,
        latency_ms=round((time.perf_counter() - started) * 1000),
        called_at=datetime.now(timezone.utc),
    )
    return {"tool_calls": [call]}
