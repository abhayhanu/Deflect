"""The approval flow survives a killed process.

One process runs a ticket until it pauses for approval and is then killed by the test, with no
chance to clean up. A second, brand new process records the approval and resumes the run from
the Postgres checkpoint. The refund must run exactly once, authorized by the person who
approved it, and the audit log must show it.
"""

import json
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import psycopg
import pytest

from data import testdb

REPO = Path(__file__).resolve().parent.parent

SETUP = """
import json, sys, time
from datetime import datetime, timezone
from agent.approvals import PostgresApprovals, decide_and_resume
from agent.conftest import FakeTools, ScriptedClient
from agent.context import RunContext
from agent.graph import build_graph, postgres_checkpointer
from agent.guardrails.audit import PostgresAuditLog
from agent.providers import ChatModel

TICKET = sys.argv[1]
NOW = datetime(2026, 9, 24, 6, 30, tzinfo=timezone.utc)
ORDER = {"order_id": "A4580", "customer_id": "C_1154", "status": "delivered", "total_inr": 3499.0,
         "refunded_inr": 0.0, "placed_at": "2026-09-14T06:30:00+00:00", "delivered_at": "2026-09-20T06:30:00+00:00",
         "shipments": [], "refunds": [], "items": [], "shipping_city": "Bengaluru"}
TOOLS = FakeTools({"search_policy": [{"doc_id": "pol_lost_transit", "title": "Lost", "chunk": "Refund it.", "score": 1.0}],
                   "get_order": ORDER, "issue_refund": {"refund_id": "RF-A4580-1", "amount_inr": 3499.0}})
PLAN = {"decision": "act", "tool_name": "issue_refund", "cites": ["pol_lost_transit"], "rationale": "Lost parcel.",
        "tool_args": {"order_id": "A4580", "amount_inr": 3499, "reason_code": "lost_in_transit",
                      "policy_doc_id": "pol_lost_transit"}}
CLASSIFICATION = {"intent": "refund_request", "confidence": 0.9, "urgency": "medium", "sentiment": "neutral",
                  "order_id": "A4580", "reasoning": "lost"}
MODEL = ChatModel("fake", "scripted", ScriptedClient(
    {"Classification": [CLASSIFICATION], "Plan": [PLAN], "ClaimCheck": [{"unsupported_claims": []}]},
    texts=["Refund RF-A4580-1 of Rs 3,499 is on its way."]))
ctx = RunContext(model=MODEL, now=NOW, tools=TOOLS, audit=PostgresAuditLog(), approvals=PostgresApprovals())
"""

FIRST = SETUP + """
with postgres_checkpointer() as saver:
    graph = build_graph(saver)
    config = {"configurable": {"thread_id": TICKET}}
    graph.invoke({"ticket_id": TICKET, "raw_message": "Order A4580 never came. Refund please.",
                  "customer_id": "C_1154", "channel": "chat"}, config, context=ctx)
    print(json.dumps({"next": graph.get_state(config).next, "approval_id": graph.get_state(config).values["approval_id"]}),
          flush=True)
    time.sleep(120)
"""

SECOND = SETUP + """
approval_id = sys.argv[2]
with postgres_checkpointer() as saver:
    final = decide_and_resume(build_graph(saver), PostgresApprovals(), approval_id, True, "priya", ctx)
print(json.dumps({"terminal_reason": final["terminal_reason"], "refunds_sent": len([c for c in TOOLS.calls if c[0] == "issue_refund"]),
                  "authorized_by": final["tool_calls"][-1].authorized_by, "approver": final["tool_calls"][-1].approver_id}),
      flush=True)
"""


@pytest.fixture
def database(monkeypatch):
    url = testdb.prepare(monkeypatch)
    if url is None:
        pytest.skip("Postgres is not running")
    return url


def audit_rows(url: str, ticket: str) -> list[tuple]:
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT tool_name, authorized_by, approver_id FROM audit_log "
                            "WHERE ticket_id = %s AND tool_name = 'issue_refund'", (ticket,)).fetchall()


def test_an_approval_survives_a_killed_process(database):
    ticket = f"durable_{uuid.uuid4().hex[:8]}"
    first = subprocess.Popen([sys.executable, "-c", textwrap.dedent(FIRST), ticket], cwd=REPO,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = first.stdout.readline()
        assert line, first.stderr.read()
        paused = json.loads(line)
    finally:
        first.kill()
        first.wait(timeout=30)

    assert paused["next"] == ["await_approval"]
    assert audit_rows(database, ticket) == []

    second = subprocess.run([sys.executable, "-c", textwrap.dedent(SECOND), ticket, paused["approval_id"]], cwd=REPO,
                            capture_output=True, text=True, timeout=120)
    assert second.returncode == 0, second.stderr
    finished = json.loads(second.stdout.strip().splitlines()[-1])

    assert finished == {"terminal_reason": "acted", "refunds_sent": 1, "authorized_by": "human", "approver": "priya"}
    assert audit_rows(database, ticket) == [("issue_refund", "human", "priya")]
    with psycopg.connect(database) as conn:
        status = conn.execute("SELECT status, approver_id FROM approvals WHERE approval_id = %s",
                              (paused["approval_id"],)).fetchone()
    assert status == ("approved", "priya")
