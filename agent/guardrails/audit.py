"""The append only audit log.

One row for every tool call the agent makes, every guardrail denial and every human decision.
The writer connects as the agent's own database role, which may INSERT and SELECT on the table
and nothing else, so even a bug here cannot rewrite history. The grants live in the first
migration, not in this file.

Arguments are stored with placeholders, never the real personal details. Reads keep only a
short summary of what came back, so the log never becomes a second copy of customer data.

If a row cannot be written the run stops. A tool call that left no trace must never look finished.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from psycopg.types.json import Jsonb

log = logging.getLogger("agent.guardrails.audit")

INSERT = """
INSERT INTO audit_log (ticket_id, trace_id, tool_name, tool_args, result, error, authorized_by,
                       approver_id, policy_doc_id, check_name, latency_ms, cost_inr, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


class AuditError(RuntimeError):
    pass


@dataclass
class AuditEntry:
    ticket_id: str
    trace_id: str
    tool_name: str
    tool_args: dict
    authorized_by: str
    result: dict | list | None = None
    error: str | None = None
    approver_id: str | None = None
    check_name: str | None = None
    latency_ms: int = 0
    cost_inr: float | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def policy_doc_id(self) -> str | None:
        return self.tool_args.get("policy_doc_id")


class AuditLog(Protocol):
    def record(self, entry: AuditEntry) -> None: ...
    def for_ticket(self, ticket_id: str) -> list[AuditEntry]: ...


READ = ("SELECT ticket_id, trace_id, tool_name, tool_args, authorized_by, result, error, approver_id, check_name, "
        "latency_ms, cost_inr, created_at FROM audit_log WHERE ticket_id = %s ORDER BY id")


class PostgresAuditLog:
    def __init__(self, url: str | None = None):
        from data.db import app_database_url

        self.url = url or app_database_url()

    def record(self, entry: AuditEntry) -> None:
        import psycopg

        values = (
            entry.ticket_id, entry.trace_id, entry.tool_name, Jsonb(entry.tool_args),
            None if entry.result is None else Jsonb(entry.result), entry.error, entry.authorized_by,
            entry.approver_id, entry.policy_doc_id, entry.check_name, entry.latency_ms, entry.cost_inr,
            entry.created_at,
        )
        try:
            with psycopg.connect(self.url, autocommit=True, connect_timeout=5) as conn:
                conn.execute(INSERT, values)
        except psycopg.Error as exc:
            raise AuditError(f"could not write the audit row for {entry.tool_name}: {exc}") from exc

    def for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        """Every row a ticket left behind, oldest first. This is what the console's run view shows."""
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row, connect_timeout=5) as conn:
            rows = conn.execute(READ, (ticket_id,)).fetchall()
        return [AuditEntry(**{**row, "cost_inr": None if row["cost_inr"] is None else float(row["cost_inr"])}) for row in rows]


class MemoryAuditLog:
    """Keeps rows in a list. For tests, and for runs where no database is wanted."""

    def __init__(self):
        self.rows: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.rows.append(entry)

    def for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        return [row for row in self.rows if row.ticket_id == ticket_id]


def summarise_read(result) -> dict:
    """What a read is allowed to leave in the log: whether something came back, and how much."""
    if result is None:
        return {"found": False}
    if isinstance(result, list):
        return {"found": bool(result), "count": len(result)}
    keys = [k for k in ("order_id", "customer_id", "status") if isinstance(result, dict) and k in result]
    return {"found": True, **{k: result[k] for k in keys}}
