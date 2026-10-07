import logging

from langgraph.runtime import Runtime

from agent.context import RunContext
from agent.guardrails.policy import REFUND_ARRIVAL
from agent.guardrails.redact import drop_unknown_placeholders, rehydrate, unknown_placeholders
from agent.state import TicketState

log = logging.getLogger("agent.nodes.respond")

TOPIC = {
    "order_status": "your question about your order",
    "refund_request": "your refund request",
    "return_request": "your return request",
    "address_change": "your address change request",
    "cancellation": "your cancellation request",
    "complaint": "your complaint",
    "product_question": "your question",
}


def rupees(amount) -> str:
    value = float(amount)
    return f"Rs {value:,.0f}" if value.is_integer() else f"Rs {value:,.2f}"


def arrives(method: str | None) -> str:
    arrival = REFUND_ARRIVAL.get(method or "")
    return f" It reaches you {arrival.phrase}." if arrival else ""


def already_done(state: TicketState) -> str:
    """What was really done before the ticket went to a person, built only from the tool result.

    v4 refunded a customer Rs 3,049, the checker then rejected the wording of the reply three
    times, and the customer was told only that the ticket was passed on. They were never told
    their money was on its way.
    """
    calls = [c for c in state.get("tool_calls") or [] if not c.error and c.result]
    if not calls:
        return ""
    call, r = calls[-1], calls[-1].result
    order = r.get("order_id") or call.args.get("order_id")
    if call.name == "issue_refund" and r.get("refund_id"):
        return (f" Your refund of {rupees(r['amount_inr'])} for order {order} has been issued, reference "
                f"{r['refund_id']}.{arrives(r.get('payment_method'))}")
    if call.name == "cancel_order":
        refund = r.get("refund") or {}
        text = f" Your order {order} has been cancelled."
        if refund.get("refund_id"):
            text += (f" A refund of {rupees(refund['amount_inr'])} has been issued, reference {refund['refund_id']}."
                     f"{arrives(r.get('payment_method'))}")
        return text
    if call.name == "update_shipping_address":
        return f" The delivery address on order {order} has been updated."
    if call.name == "create_return_label" and r.get("label_id"):
        return f" A return pickup has been booked for order {order}, label {r['label_id']}."
    return ""


def escalation_reply(state: TicketState) -> str:
    # Escalations get a fixed reply so the agent can never promise an outcome it does not own.
    c = state.get("classification")
    case = (state.get("escalation") or {}).get("case") or {}
    hours = case.get("respond_within_hours") or (4 if c and c.urgency == "high" else 24)
    topic = TOPIC.get(c.intent, "your message") if c else "your message"
    reference = f" Your reference is {case['escalation_id']}." if case.get("escalation_id") else ""
    done = already_done(state)
    passed = "I have also passed your message" if done else "I have passed it"
    return (
        f"Hi, thank you for getting in touch about {topic}.{done} {passed} to our support team "
        f"and they will reply within {hours} hours.{reference} There is nothing else you need to do for now.\n\n"
        "Deflect Support"
    )


def respond(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    """The last step. Escalations get the fixed reply. Everything else sends the checked draft,
    with the real personal details put back only now."""
    reason = state.get("terminal_reason") or ""
    if reason.startswith("escalated"):
        return {"reply": escalation_reply(state), "terminal_reason": reason}

    acted = any(not call.error for call in state.get("tool_calls") or [])
    reply = rehydrate(state["draft"], runtime.context.pii_map(state["raw_message"]))
    if unknown_placeholders(reply):
        log.warning("Reply used placeholders that match nothing in the ticket: %s", unknown_placeholders(reply))
        reply = drop_unknown_placeholders(reply)
    return {"reply": reply, "terminal_reason": "acted" if acted else "answered"}
