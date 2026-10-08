"""The inbox: one small row per ticket, so the console can list tickets without opening every run.

The run itself lives in the LangGraph checkpoints and stays the truth. A row here is a summary
written after each run and each resume, and it only ever holds the redacted message. If a row
and its run disagree, the row is rebuilt from the run.
"""

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Protocol

from agent.guardrails.redact import RedactionError, redact

PREVIEW_CHARS = 240


@dataclass
class TicketRow:
    ticket_id: str
    channel: str
    preview: str
    status: str
    customer_id: str | None = None
    intent: str | None = None
    decision: str | None = None
    terminal_reason: str | None = None
    cost_inr: float = 0.0
    latency_ms: int | None = None
    source: str = "api"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def status_of(snapshot) -> str:
    if snapshot.next == ("await_approval",):
        return "awaiting_approval"
    return "stopped" if snapshot.next else "done"


def placeholders_only(text: str | None) -> str:
    """Whatever is shown in the console goes through redaction once more on the way out."""
    try:
        return redact(text or "")[0]
    except RedactionError:
        return "[removed, redaction failed]"


def summarise(ticket_id: str, snapshot, source: str = "api", latency_ms: int | None = None) -> TicketRow:
    values = snapshot.values
    c, plan = values.get("classification"), values.get("plan")
    reason = values.get("terminal_reason")
    escalated = (reason or "").startswith("escalated")
    message = values.get("redacted_message") or placeholders_only(values.get("raw_message"))
    return TicketRow(
        ticket_id=ticket_id, channel=values.get("channel") or "web_form", customer_id=values.get("customer_id"),
        preview=message[:PREVIEW_CHARS], status=status_of(snapshot), intent=c.intent if c else None,
        decision="escalate" if escalated else (plan.decision if plan else None), terminal_reason=reason,
        cost_inr=round(values.get("cost_inr", 0.0), 4), latency_ms=latency_ms, source=source,
    )


class TicketIndex(Protocol):
    def save(self, row: TicketRow) -> None: ...
    def get(self, ticket_id: str) -> TicketRow | None: ...
    def recent(self, limit: int = 100) -> list[TicketRow]: ...


COLUMNS = [f.name for f in fields(TicketRow)]
# A second save keeps when the ticket first arrived and where it came from.
KEPT = ("ticket_id", "created_at", "source")


class PostgresTickets:
    def __init__(self, url: str | None = None):
        from data.db import app_database_url

        self.url = url or app_database_url()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row, connect_timeout=5)

    def save(self, row: TicketRow) -> None:
        updates = ", ".join(f"{c} = COALESCE(EXCLUDED.{c}, tickets.{c})" if c == "latency_ms" else f"{c} = EXCLUDED.{c}"
                            for c in COLUMNS if c not in KEPT)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO tickets ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))}) "
                f"ON CONFLICT (ticket_id) DO UPDATE SET {updates}",
                [getattr(row, c) for c in COLUMNS],
            )

    def get(self, ticket_id: str) -> TicketRow | None:
        with self._connect() as conn:
            found = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM tickets WHERE ticket_id = %s", (ticket_id,)).fetchone()
        return as_row(found) if found else None

    def recent(self, limit: int = 100) -> list[TicketRow]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM tickets ORDER BY created_at DESC, ticket_id LIMIT %s",
                                (limit,)).fetchall()
        return [as_row(r) for r in rows]


def as_row(found: dict) -> TicketRow:
    return TicketRow(**{**found, "cost_inr": float(found["cost_inr"])})


class MemoryTickets:
    def __init__(self):
        self.rows: dict[str, TicketRow] = {}

    def save(self, row: TicketRow) -> None:
        earlier = self.rows.get(row.ticket_id)
        if earlier:
            row.created_at, row.source = earlier.created_at, earlier.source
            row.latency_ms = row.latency_ms if row.latency_ms is not None else earlier.latency_ms
        self.rows[row.ticket_id] = row

    def get(self, ticket_id: str) -> TicketRow | None:
        return self.rows.get(ticket_id)

    def recent(self, limit: int = 100) -> list[TicketRow]:
        return sorted(self.rows.values(), key=lambda r: r.created_at, reverse=True)[:limit]
