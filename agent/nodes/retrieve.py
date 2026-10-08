import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from langgraph.runtime import Runtime

from agent.context import AuditedTools, RunContext
from agent.guardrails.policy import REFUND_ARRIVAL
from agent.state import RetrievedPolicy, TicketState

log = logging.getLogger("agent.nodes.retrieve")

# How many sections the search returns. It was 5 until v9. At 5 the search missed a required
# policy for 2 of 114 golden tickets even with the escalation policy pinned, and at 8 for none.
TOP_K = 8
QUERY_LIMIT = 4000


def hours_since(now: datetime, iso: str | None) -> float | None:
    if not iso:
        return None
    return round((now - datetime.fromisoformat(iso)).total_seconds() / 3600, 1)


def order_facts(order: dict, customer_id: str | None, now: datetime) -> dict:
    """Adds the numbers the policies are written in, so the model never does date maths."""
    if customer_id and order["customer_id"] != customer_id:
        # The model cannot leak details it never sees.
        return {"order_id": order["order_id"], "belongs_to_customer": False,
                "note": "This order is on another customer's account."}

    facts = {**order, "belongs_to_customer": True}
    facts["refundable_inr"] = round(order["total_inr"] - order["refunded_inr"], 2)
    arrival = REFUND_ARRIVAL.get(order.get("payment_method"))
    if arrival:
        facts["refund_reaches_customer"] = arrival.phrase
    facts["hours_since_placed"] = hours_since(now, order["placed_at"])
    facts["hours_since_delivered"] = hours_since(now, order["delivered_at"])

    forward = next((s for s in order["shipments"] if s["direction"] == "forward"), None)
    if forward and forward["promised_by"]:
        end = datetime.fromisoformat(forward["delivered_at"]) if forward["delivered_at"] else now
        facts["hours_past_promised_date"] = max(0.0, hours_since(end, forward["promised_by"]))

    for refund in facts["refunds"]:
        refund["days_ago"] = round(hours_since(now, refund["issued_at"]) / 24, 1)
    return facts


def history_facts(history: dict, now: datetime) -> dict:
    for refund in history["refunds"]:
        refund["days_ago"] = round(hours_since(now, refund["issued_at"]) / 24, 1)
    return history


def read(tools: AuditedTools, name: str, args: dict) -> dict | None:
    # A missing or malformed id is usually a typo, not an error. Plan asks the customer to check it.
    outcome = tools.call(name, args)
    if outcome.error:
        log.warning("%s refused %s: %s", name, args, outcome.error)
        return None
    return outcome.result


def retrieve(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    classification = state["classification"]
    ctx = runtime.context
    tools = ctx.tools_for(state)
    customer_id = state.get("customer_id")
    search = {"query": state["redacted_message"][:QUERY_LIMIT], "intent": classification.intent, "top_k": TOP_K}

    with ThreadPoolExecutor(max_workers=3) as pool:
        hits = pool.submit(tools.call, "search_policy", search)
        order = pool.submit(read, tools, "get_order", {"order_id": classification.order_id}) if classification.order_id else None
        history = pool.submit(read, tools, "get_customer_history", {"customer_id": customer_id}) if customer_id else None
        found = hits.result()
        order = order.result() if order else None
        history = history.result() if history else None

    if found.error:
        raise RuntimeError(f"search_policy failed: {found.error}")
    now = ctx.clock()
    return {
        "policies": [RetrievedPolicy(**hit) for hit in found.result],
        "order": order_facts(order, customer_id, now) if order else None,
        "customer_history": history_facts(history, now) if history else None,
    }
