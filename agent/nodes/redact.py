from langgraph.runtime import Runtime

from agent.context import RunContext
from agent.guardrails.redact import redact as scrub
from agent.state import TicketState


def redact(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    text, mapping = scrub(state["raw_message"])
    runtime.context.rehydration.update(mapping)
    return {"redacted_message": text}
