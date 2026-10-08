import pytest

from mcp_server.tests.conftest import call, query

KEY = "test:tool:0000000000000001"


def test_server_lists_nine_tools_with_risk_classes():
    from mcp_server.server import TOOLS

    risks = {fn.__name__: risk for fn, risk in TOOLS}
    assert risks == {
        "get_order": "read", "get_customer_history": "read", "search_policy": "read", "check_shipment": "read",
        "update_shipping_address": "write_low", "create_return_label": "write_low", "cancel_order": "write_low",
        "issue_refund": "write_high", "escalate_to_human": "write_low",
    }


def test_get_order_returns_shipments_refunds_and_no_street_address():
    order = call("get_order", {"order_id": "A8842"}).data
    assert order["total_inr"] == 2400.0
    assert [s["direction"] for s in order["shipments"]] == ["forward"]
    assert "shipping_address" not in order and order["shipping_city"] == "Bengaluru"


def test_missing_order_is_null_not_an_error():
    reply = call("get_order", {"order_id": "A0000"})
    assert reply.error is None and reply.data is None


def test_history_includes_refunds_across_orders():
    history = call("get_customer_history", {"customer_id": "C_1189"}).data
    assert any(r["reason_code"] == "lost_in_transit" for r in history["refunds"])
    assert call("get_customer_history", {"customer_id": "C_0000"}).data is None


def test_check_shipment():
    tracking = call("check_shipment", {"order_id": "A3107"}).data
    assert tracking["order_status"] == "shipped"
    assert tracking["shipments"][0]["last_event"] == "In transit"


def test_search_is_filtered_by_intent(indexed):
    hits = call("search_policy", {"query": "change my delivery address", "intent": "address_change"}).data
    assert {h["doc_id"] for h in hits} <= {"pol_address_change", "pol_escalation"}
    assert hits[0]["doc_id"] == "pol_address_change"
    assert set(hits[0]) == {"doc_id", "title", "chunk", "score", "pinned"}


def sections_of(hits, doc_id):
    return [h["chunk"].split("\n## ", 1)[1].split("\n", 1)[0] for h in hits if h["doc_id"] == doc_id]


def test_the_policy_that_overrides_the_others_comes_back_whatever_was_asked(indexed):
    hits = call("search_policy", {"query": "where is my order, it has not arrived", "intent": "order_status", "top_k": 2}).data
    ranked, added = hits[:2], hits[2:]
    assert not any(h["pinned"] for h in ranked)
    assert added and all(h["pinned"] and h["doc_id"] == "pol_escalation" for h in added)
    assert sorted(sections_of(hits, "pol_escalation")) == ["Always escalate", "Overview", "What the customer is told"]
    assert len({h["chunk"] for h in hits}) == len(hits)


def test_a_pinned_section_the_search_ranked_is_not_returned_twice(indexed):
    asked = "a lawyer, a legal notice, a consumer court or a police complaint always go to the support team"
    hits = call("search_policy", {"query": asked, "intent": "complaint", "top_k": 3}).data
    assert hits[0]["doc_id"] == "pol_escalation" and hits[0]["pinned"] is False
    assert sorted(sections_of(hits, "pol_escalation")) == ["Always escalate", "Overview", "What the customer is told"]


def refund(**changes):
    args = {"order_id": "A8842", "amount_inr": 2400, "reason_code": "lost_in_transit",
            "policy_doc_id": "pol_lost_transit", "idempotency_key": KEY}
    return call("issue_refund", {**args, **changes})


def test_refund_is_issued_and_recorded(fresh_db):
    reply = refund()
    assert reply.error is None
    assert reply.data["refund_id"] == "RF-A8842-1"
    assert reply.data["payment_method"] == "card" and reply.data["still_refundable_inr"] == 0.0
    assert query("SELECT refunded_inr FROM orders WHERE order_id = 'A8842'")[0]["refunded_inr"] == 2400


@pytest.mark.parametrize("changes, code", [
    ({"amount_inr": 2400.5}, "exceeds_refundable"),
    ({"order_id": "A2040", "amount_inr": 26000, "reason_code": "damaged", "policy_doc_id": "pol_damaged_goods"}, "over_ceiling"),
    ({"policy_doc_id": "pol_free_money"}, "ungrounded"),
    ({"order_id": "A3448", "amount_inr": 350}, "too_early"),
    ({"order_id": "A5925", "amount_inr": 3000}, "not_lost"),
    ({"order_id": "A0000"}, "not_found"),
])
def test_refund_rejections(fresh_db, changes, code):
    reply = refund(**changes)
    assert reply.error.startswith(f"{code}:"), reply.error
    assert query("SELECT count(*) AS n FROM refunds WHERE idempotency_key = %s", (KEY,))[0]["n"] == 0


def test_partial_refund_respects_what_was_already_refunded(fresh_db):
    assert refund(order_id="A2594", amount_inr=1900, reason_code="damaged", policy_doc_id="pol_damaged_goods").error
    reply = refund(order_id="A2594", amount_inr=1899, reason_code="damaged", policy_doc_id="pol_damaged_goods")
    assert reply.error is None and reply.data["refund_id"] == "RF-A2594-2"


def test_a_rejected_call_does_not_use_up_its_key(fresh_db):
    assert refund(amount_inr=9999).error.startswith("exceeds_refundable")
    assert refund().error is None


def test_cancel_prepaid_order_refunds_it_once(fresh_db):
    reply = call("cancel_order", {"order_id": "A7262", "idempotency_key": KEY}).data
    assert reply["status"] == "cancelled" and reply["refund"]["amount_inr"] == 5000.0
    assert reply["refund"]["reason_code"] == "cancellation"
    assert call("cancel_order", {"order_id": "A7262", "idempotency_key": KEY + "2"}).error.startswith("wrong_status")


def test_cancel_cash_on_delivery_has_nothing_to_refund(fresh_db):
    assert call("cancel_order", {"order_id": "A7483", "idempotency_key": KEY}).data["refund"] is None


def test_shipped_order_cannot_be_cancelled(fresh_db):
    assert call("cancel_order", {"order_id": "A3107", "idempotency_key": KEY}).error.startswith("wrong_status")


def address(**changes):
    args = {"order_id": "A6472", "new_address": "Flat 12B, Palm Grove, Koramangala, Bengaluru 560095",
            "city": "Bengaluru", "idempotency_key": KEY}
    return call("update_shipping_address", {**args, **changes})


def test_address_change_is_stored_but_not_echoed(fresh_db):
    reply = address()
    assert reply.error is None and "new_address" not in reply.data
    stored = query("SELECT shipping_address FROM orders WHERE order_id = 'A6472'")[0]["shipping_address"]
    assert stored.startswith("Flat 12B, Palm Grove")


def test_address_change_rules(fresh_db):
    assert address(city="Delhi").error.startswith("different_city")
    assert address(order_id="A6931", city="Delhi").error.startswith("wrong_status")
    big = query("SELECT order_id, shipping_city FROM orders WHERE status = 'placed' AND total_inr > 25000 LIMIT 1")[0]
    assert address(order_id=big["order_id"], city=big["shipping_city"]).error.startswith("needs_verification")


def test_return_label(fresh_db):
    reply = call("create_return_label", {"order_id": "A5401", "idempotency_key": KEY}).data
    assert reply["label_id"] == "RL-A5401" and reply["fee_inr"] == 99.0
    again = call("create_return_label", {"order_id": "A5401", "idempotency_key": KEY + "2"})
    assert again.error.startswith("already_returned")


def test_return_window_and_status(fresh_db):
    assert call("create_return_label", {"order_id": "A2045", "idempotency_key": KEY}).error.startswith("outside_window")
    assert call("create_return_label", {"order_id": "A3107", "idempotency_key": KEY}).error.startswith("wrong_status")
    damaged = call("create_return_label", {"order_id": "A4108", "idempotency_key": KEY, "reason": "damaged"})
    assert damaged.data["fee_inr"] == 0.0


def test_escalation_opens_a_case(fresh_db):
    reply = call("escalate_to_human", {"ticket_id": "gold_101", "reason": "safety", "priority": "urgent",
                                       "summary": "Battery swelling reported on a delivered phone.",
                                       "order_id": "A8842", "idempotency_key": KEY}).data
    assert reply["respond_within_hours"] == 4 and reply["status"] == "open"
    assert query("SELECT count(*) AS n FROM escalations")[0]["n"] == 1
