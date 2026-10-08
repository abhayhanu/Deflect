"""Running a ticket for the API, and keeping the inbox and the budget in step with it.

Every run and every resume goes through here, whether it came from a person using the console
or from the demo loader. Each one is a single trace, is filed in the inbox whatever happened to
it, and adds what its model calls cost to today's budget.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from agent import tracing
from agent.approvals import ApprovalStore, decide_and_resume
from agent.context import RunContext
from api.limits import Budget
from api.tickets import MemoryTickets, TicketIndex, TicketRow, summarise

log = logging.getLogger("api.runs")


@dataclass
class Services:
    graph: Any
    approvals: ApprovalStore
    context: Callable[[], RunContext]
    tickets: TicketIndex = field(default_factory=MemoryTickets)
    budget: Budget = field(default_factory=lambda: Budget(None))
    # Puts the shop data back to its seeded state and empties the inbox. Only the demo uses it.
    reseed: Callable[[], None] | None = None


def thread(ticket_id: str) -> dict:
    return {"configurable": {"thread_id": ticket_id}}


def file_ticket(s: Services, ticket_id: str, source: str, started: float, cost_before: float) -> TicketRow | None:
    """Writes the inbox row from the run as it now stands, and counts what this step cost."""
    snapshot = s.graph.get_state(thread(ticket_id))
    if not snapshot.values:
        return None
    earlier = s.tickets.get(ticket_id)
    took = round((time.perf_counter() - started) * 1000) + ((earlier.latency_ms or 0) if earlier else 0)
    row = summarise(ticket_id, snapshot, source, latency_ms=took)
    s.tickets.save(row)
    s.budget.add(row.cost_inr - cost_before)
    return row


def run_ticket(s: Services, ticket: dict, source: str = "api") -> None:
    """Runs a new ticket to its end, or to the point where it waits for a person."""
    ctx = s.context()
    config = thread(ticket["ticket_id"])
    started = time.perf_counter()
    try:
        with tracing.ticket_trace(ticket["ticket_id"]) as run:
            s.graph.invoke({**ticket, "received_at": ctx.clock(), **run.ids()}, config, context=ctx)
            run.finish(s.graph.get_state(config).values)
    finally:
        file_ticket(s, ticket["ticket_id"], source, started, 0.0)


def resume_ticket(s: Services, approval_id: str, approved: bool, approver_id: str, note: str | None, ticket_id: str) -> None:
    """Records a person's decision and lets the paused run carry on."""
    earlier = s.tickets.get(ticket_id)
    started = time.perf_counter()
    try:
        decide_and_resume(s.graph, s.approvals, approval_id, approved, approver_id, s.context(), note)
    finally:
        file_ticket(s, ticket_id, earlier.source if earlier else "api", started, earlier.cost_inr if earlier else 0.0)


def refreshed(s: Services, row: TicketRow) -> TicketRow:
    """A ticket approved from the terminal never passed through here, so a row that still
    says it is waiting is checked against its run before it is shown."""
    if row.status != "awaiting_approval":
        return row
    snapshot = s.graph.get_state(thread(row.ticket_id))
    if not snapshot.values:
        return row
    current = summarise(row.ticket_id, snapshot, row.source)
    if current.status == row.status:
        return row
    s.tickets.save(current)
    return s.tickets.get(row.ticket_id) or current
