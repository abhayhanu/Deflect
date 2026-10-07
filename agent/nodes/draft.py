import json

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from agent.context import RunContext
from agent.providers import call_text
from agent.state import TicketState

SYSTEM = """You write the reply to a customer of an Indian online shop.

Use only the facts in the decision notes, the action result, the policy excerpts and the
order details. Never promise anything the excerpts do not support, such as compensation, a
call back or faster delivery. If the notes say to ask for the order id, ask for it.
If an action was taken, confirm what the action result shows, with its amounts and ids, and
say what happens next according to the excerpts. Never claim an action the result does not show.

Keep it under 120 words. Plain text, no markdown, no subject line. Start with "Hi,". Write
amounts like Rs 2,400. Personal details in the customer message were replaced by placeholders
such as <EMAIL_1>. If you repeat one of those details, copy its placeholder exactly. Never use
a placeholder as the customer's name and never make one up. Sign off as Deflect Support."""


def action_taken(state: TicketState) -> str:
    calls = [c for c in state.get("tool_calls") or [] if not c.error]
    if not calls:
        return ""
    last = calls[-1]
    args = {k: v for k, v in last.args.items() if k != "idempotency_key"}
    return f"\n\nAction taken:\n{last.name} with {json.dumps(args)}\nResult: {json.dumps(last.result)}"


def cited_policies(state: TicketState) -> list:
    cites = state["plan"].cites
    return [p for p in state.get("policies", []) if p.doc_id in cites] or state.get("policies", [])


def feedback(state: TicketState) -> str:
    """When the checker sent the last reply back, the next one is told exactly why."""
    check = state.get("verification")
    if not check or check.verdict != "retry" or not check.unsupported_claims:
        return ""
    lines = "\n".join(f"- {claim}" for claim in check.unsupported_claims)
    return f"\n\nA checker rejected the previous reply because these statements are not supported:\n{lines}\nLeave them out."


def draft_prompt(state: TicketState) -> list:
    excerpts = "\n\n".join(f"[doc_id: {p.doc_id}]\n{p.chunk}" for p in cited_policies(state))
    body = f"""Customer message:
{state["redacted_message"]}

Decision notes:
{state["plan"].rationale}{action_taken(state)}

Order details:
{json.dumps(state.get("order"))}

Policy excerpts:
{excerpts or "None"}{feedback(state)}"""
    return [SystemMessage(SYSTEM), HumanMessage(body)]


def draft(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    """Writes the reply with placeholders still in it. The checker reads this version, so it
    never sees a real email or address, and respond fills the real values in at the very end."""
    ctx = runtime.context
    spent_before = ctx.usage.cost_inr
    text = call_text(ctx.chat_model(), draft_prompt(state), ctx.usage)
    return {"draft": text, "cost_inr": state.get("cost_inr", 0.0) + ctx.usage.cost_inr - spent_before}
