from datetime import datetime, timezone
from types import SimpleNamespace

import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege

from agent.guardrails.audit import AuditEntry, PostgresAuditLog
from agent.state import Classification, Plan
from api.tickets import MemoryTickets, PostgresTickets, TicketRow, summarise
from data import testdb
from data.db import app_database_url


def snapshot(next_nodes=(), **values):
    return SimpleNamespace(next=tuple(next_nodes), values=values)


def test_a_row_is_built_from_the_run_and_only_from_its_redacted_message():
    c = Classification(intent="refund_request", confidence=0.9, urgency="medium", sentiment="neutral", order_id="A1001", reasoning="r")
    plan = Plan(decision="act", tool_name="issue_refund", tool_args={}, cites=["pol_lost_transit"], rationale="r")
    waiting = summarise("T1", snapshot(["await_approval"], raw_message="mail me at a@b.co", redacted_message="mail me at <EMAIL_1>",
                                       classification=c, plan=plan, cost_inr=0.12345, channel="chat"))
    assert (waiting.status, waiting.intent, waiting.decision, waiting.preview) == ("awaiting_approval", "refund_request", "act", "mail me at <EMAIL_1>")
    assert waiting.cost_inr == 0.1235

    escalated = summarise("T1", snapshot(classification=c, plan=plan, terminal_reason="escalated_guardrail_allowlist", redacted_message="x"))
    assert (escalated.status, escalated.decision) == ("done", "escalate")
    # A run that died before redact has no redacted message yet, and the raw one is still never shown.
    crashed = summarise("T1", snapshot(["redact"], raw_message="mail me at a@b.co"))
    assert (crashed.status, crashed.preview, crashed.intent) == ("stopped", "mail me at <EMAIL_1>", None)


@pytest.fixture(params=["memory", "postgres"])
def tickets(request, monkeypatch):
    if request.param == "memory":
        return MemoryTickets()
    if testdb.prepare(monkeypatch) is None:
        pytest.skip("Postgres is not running")
    with psycopg.connect(testdb.isolated_url(), autocommit=True) as owner:
        owner.execute("TRUNCATE tickets")
    return PostgresTickets()


def test_a_second_save_updates_the_row_and_keeps_when_it_first_arrived(tickets):
    first = TicketRow("T1", "chat", "where is it", "awaiting_approval", intent="refund_request", cost_inr=0.1, latency_ms=900,
                      source="demo", created_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    tickets.save(first)
    tickets.save(TicketRow("T1", "chat", "where is it", "done", intent="refund_request", decision="act", terminal_reason="acted", cost_inr=0.3))
    tickets.save(TicketRow("T2", "email", "second", "done", created_at=datetime(2026, 10, 2, tzinfo=timezone.utc)))

    row = tickets.get("T1")
    assert (row.status, row.terminal_reason, row.cost_inr, row.latency_ms) == ("done", "acted", 0.3, 900)
    assert (row.source, row.created_at) == ("demo", datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert [r.ticket_id for r in tickets.recent()] == ["T2", "T1"] and tickets.get("nothing") is None


def test_the_agents_role_can_file_tickets_but_not_remove_them(monkeypatch):
    if testdb.prepare(monkeypatch) is None:
        pytest.skip("Postgres is not running")
    PostgresTickets().save(TicketRow("T_grant", "chat", "x", "done"))
    with psycopg.connect(app_database_url(), autocommit=True) as app:
        for statement in ("DELETE FROM tickets", "TRUNCATE tickets"):
            with pytest.raises(InsufficientPrivilege):
                app.execute(statement)


def test_the_audit_log_can_be_read_back_for_one_ticket(monkeypatch):
    if testdb.prepare(monkeypatch) is None:
        pytest.skip("Postgres is not running")
    log = PostgresAuditLog()
    log.record(AuditEntry("T_read", "trace1", "get_order", {"order_id": "A1001"}, "policy", result={"found": True}, latency_ms=4))
    log.record(AuditEntry("T_read", "trace1", "issue_refund", {"order_id": "A1001"}, "denied", check_name="allowlist", error="not allowed"))
    log.record(AuditEntry("T_other", "trace2", "get_order", {"order_id": "A1002"}, "policy"))
    rows = log.for_ticket("T_read")
    assert [(r.tool_name, r.authorized_by, r.check_name) for r in rows][-2:] == [("get_order", "policy", None), ("issue_refund", "denied", "allowlist")]
    assert all(r.ticket_id == "T_read" for r in rows) and rows[-2].result == {"found": True}
