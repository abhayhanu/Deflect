"""Human approval requests for actions above the auto approve ceiling.

The guardrail node files a request and the graph pauses. A person approves or denies it later,
from the CLI or the API, and the graph resumes from its checkpoint on the same thread. The
decision is claimed with one conditional UPDATE, so two people clicking at once can never both
resume the same run.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from langgraph.types import Command
from psycopg.types.json import Jsonb

from agent import tracing


class ApprovalError(RuntimeError):
    pass


@dataclass
class ApprovalRequest:
    approval_id: str
    ticket_id: str
    thread_id: str
    tool_name: str
    tool_args: dict
    reason: str | None = None
    status: str = "pending"
    approver_id: str | None = None
    note: str | None = None
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None

    def resume_value(self) -> dict:
        return {"approval_id": self.approval_id, "approved": self.status == "approved",
                "approver_id": self.approver_id, "note": self.note}


def approval_id_for(action_key: str) -> str:
    """The same action on the same ticket always gets the same id, so a rerun files no duplicate."""
    return "apr_" + hashlib.sha256(action_key.encode()).hexdigest()[:16]


class ApprovalStore(Protocol):
    def request(self, req: ApprovalRequest) -> None: ...
    def get(self, approval_id: str) -> ApprovalRequest | None: ...
    def decide(self, approval_id: str, approved: bool, approver_id: str, note: str | None = None) -> ApprovalRequest: ...
    def pending(self) -> list[ApprovalRequest]: ...


COLUMNS = "approval_id, ticket_id, thread_id, tool_name, tool_args, reason, status, approver_id, note, requested_at, decided_at"


class PostgresApprovals:
    def __init__(self, url: str | None = None):
        from data.db import app_database_url

        self.url = url or app_database_url()

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row, connect_timeout=5)

    def request(self, req: ApprovalRequest) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO approvals (approval_id, ticket_id, thread_id, tool_name, tool_args, reason, requested_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (approval_id) DO NOTHING",
                (req.approval_id, req.ticket_id, req.thread_id, req.tool_name, Jsonb(req.tool_args), req.reason,
                 req.requested_at),
            )

    def get(self, approval_id: str) -> ApprovalRequest | None:
        with self._connect() as conn:
            row = conn.execute(f"SELECT {COLUMNS} FROM approvals WHERE approval_id = %s", (approval_id,)).fetchone()
        return ApprovalRequest(**row) if row else None

    def decide(self, approval_id: str, approved: bool, approver_id: str, note: str | None = None) -> ApprovalRequest:
        with self._connect() as conn:
            row = conn.execute(
                f"UPDATE approvals SET status = %s, approver_id = %s, note = %s, decided_at = now() "
                f"WHERE approval_id = %s AND status = 'pending' RETURNING {COLUMNS}",
                ("approved" if approved else "denied", approver_id, note, approval_id),
            ).fetchone()
        if row is None:
            raise not_decidable(self.get(approval_id), approval_id)
        return ApprovalRequest(**row)

    def pending(self) -> list[ApprovalRequest]:
        with self._connect() as conn:
            rows = conn.execute(f"SELECT {COLUMNS} FROM approvals WHERE status = 'pending' ORDER BY requested_at").fetchall()
        return [ApprovalRequest(**row) for row in rows]


class MemoryApprovals:
    def __init__(self):
        self.rows: dict[str, ApprovalRequest] = {}

    def request(self, req: ApprovalRequest) -> None:
        self.rows.setdefault(req.approval_id, req)

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.rows.get(approval_id)

    def decide(self, approval_id: str, approved: bool, approver_id: str, note: str | None = None) -> ApprovalRequest:
        req = self.rows.get(approval_id)
        if req is None or req.status != "pending":
            raise not_decidable(req, approval_id)
        req.status = "approved" if approved else "denied"
        req.approver_id, req.note, req.decided_at = approver_id, note, datetime.now(timezone.utc)
        return req

    def pending(self) -> list[ApprovalRequest]:
        return [r for r in self.rows.values() if r.status == "pending"]


def not_decidable(req: ApprovalRequest | None, approval_id: str) -> ApprovalError:
    if req is None:
        return ApprovalError(f"there is no approval request {approval_id}")
    return ApprovalError(f"{approval_id} was already {req.status} by {req.approver_id}")


def decide_and_resume(graph, store: ApprovalStore, approval_id: str, approved: bool, approver_id: str,
                      context, note: str | None = None) -> dict:
    """Records the decision, then continues the paused run from its checkpoint, inside the same
    trace as the run that paused, even when that was another process."""
    req = store.decide(approval_id, approved, approver_id, note)
    config = {"configurable": {"thread_id": req.thread_id}}
    paused = graph.get_state(config).values
    with tracing.ticket_trace(req.ticket_id, paused.get("trace_parent"), resumed=True) as run:
        final = graph.invoke(Command(resume=req.resume_value()), config, context=context)
        run.finish(final)
    return final
