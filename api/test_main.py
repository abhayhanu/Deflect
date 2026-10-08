from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agent.approvals import MemoryApprovals
from agent.conftest import FakeTools, ScriptedClient, calls_to
from agent.context import RunContext
from agent.graph import build_graph, memory_checkpointer
from agent.guardrails.audit import MemoryAuditLog
from agent.providers import ChatModel
from api.main import Services, create_app

NOW = datetime(2026, 9, 24, 6, 30, tzinfo=timezone.utc)
ORDER = {"order_id": "A4580", "customer_id": "C_1154", "status": "delivered", "total_inr": 3499.0, "refunded_inr": 0.0,
         "placed_at": "2026-09-14T06:30:00+00:00", "delivered_at": "2026-09-20T06:30:00+00:00", "shipments": [],
         "refunds": [], "items": [], "shipping_city": "Bengaluru"}
HIT = {"doc_id": "pol_lost_transit", "title": "Lost", "chunk": "Refund up to Rs 5,000.", "score": 0.9}
MESSAGE = "Smartwatch order A4580 marked delivered but I didn't get it. I want a refund."


def scripted_model():
    classification = {"intent": "refund_request", "confidence": 0.9, "urgency": "medium", "sentiment": "frustrated",
                      "order_id": "A4580", "reasoning": "lost parcel"}
    plan = {"decision": "act", "tool_name": "issue_refund", "cites": ["pol_lost_transit"], "rationale": "Lost, under Rs 5,000.",
            "escalation_reason": None, "tool_args": {"order_id": "A4580", "amount_inr": 3499, "reason_code": "lost_in_transit",
                                                     "policy_doc_id": "pol_lost_transit"}}
    client = ScriptedClient({"Classification": [classification] * 3, "Plan": [plan] * 3,
                             "ClaimCheck": [{"unsupported_claims": []}] * 3}, texts=["Refund RF-A4580-1 is done."] * 3)
    return ChatModel("fake", "scripted", client)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("DEFLECT_API_TOKEN", raising=False)
    tools = FakeTools({"search_policy": [HIT], "get_order": ORDER, "issue_refund": {"refund_id": "RF-A4580-1"},
                       "escalate_to_human": {"escalation_id": "ESC-1", "respond_within_hours": 24}})
    approvals, audit, model = MemoryApprovals(), MemoryAuditLog(), scripted_model()

    @contextmanager
    def fake_services():
        yield Services(graph=build_graph(memory_checkpointer()), approvals=approvals,
                       context=lambda: RunContext(model=model, now=NOW, tools=tools, audit=audit, approvals=approvals))

    with TestClient(create_app(fake_services)) as client:
        client.tools, client.audit = tools, audit
        yield client


def submit(api, ticket_id="T100"):
    return api.post("/tickets", json={"ticket_id": ticket_id, "raw_message": MESSAGE, "customer_id": "C_1154"})


def test_a_big_refund_waits_then_an_approval_finishes_it(api):
    paused = submit(api).json()
    assert paused["status"] == "awaiting_approval" and paused["reply"] is None
    [pending] = api.get("/approvals").json()
    assert pending["approval_id"] == paused["approval_id"] and pending["tool_args"]["amount_inr"] == 3499
    assert calls_to(api.tools, "issue_refund") == []

    done = api.post(f"/approvals/{pending['approval_id']}/approve", json={"approver_id": "priya"}).json()
    assert done["status"] == "done" and done["terminal_reason"] == "acted"
    assert done["tool_calls"][0]["authorized_by"] == "human" and done["tool_calls"][0]["approver_id"] == "priya"
    assert api.get("/approvals").json() == []
    assert api.get("/tickets/T100").json()["reply"] == "Refund RF-A4580-1 is done."


def test_a_decision_can_only_be_made_once(api):
    approval_id = submit(api).json()["approval_id"]
    assert api.post(f"/approvals/{approval_id}/deny", json={"approver_id": "priya", "note": "second claim"}).status_code == 200
    again = api.post(f"/approvals/{approval_id}/approve", json={"approver_id": "arjun"})
    assert again.status_code == 409 and "denied by priya" in again.json()["detail"]
    assert api.get("/tickets/T100").json()["terminal_reason"] == "escalated_human_denied"
    assert calls_to(api.tools, "issue_refund") == []


def test_unknown_ids_and_repeated_tickets(api):
    assert api.post("/approvals/apr_missing/approve", json={"approver_id": "priya"}).status_code == 404
    assert api.get("/tickets/nothing").status_code == 404
    submit(api)
    assert submit(api).status_code == 409


def test_bad_input_is_refused_before_the_graph_runs(api):
    assert api.post("/tickets", json={"ticket_id": "../etc", "raw_message": "hi"}).status_code == 422
    assert api.post("/approvals/x/approve", json={"approver_id": "a b; drop"}).status_code == 422


def test_a_token_is_required_when_one_is_set(api, monkeypatch):
    monkeypatch.setenv("DEFLECT_API_TOKEN", "s3cret")
    assert api.get("/approvals").status_code == 401
    assert api.get("/approvals", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get("/approvals", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert api.get("/health").status_code == 200
