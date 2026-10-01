from datetime import datetime, timezone

from agent.nodes.retrieve import order_facts

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def order(**changes):
    base = {
        "order_id": "A8842", "customer_id": "C_1182", "status": "delivered",
        "total_inr": 2400.0, "refunded_inr": 400.0,
        "placed_at": "2026-09-15T12:00:00+00:00", "delivered_at": "2026-09-20T12:00:00+00:00",
        "shipments": [{"direction": "forward", "promised_by": "2026-09-19T12:00:00+00:00",
                       "delivered_at": "2026-09-20T12:00:00+00:00"}],
        "refunds": [{"refund_id": "RF1", "issued_at": "2026-09-22T12:00:00+00:00"}],
    }
    return {**base, **changes}


def test_policy_numbers_are_computed_in_code():
    facts = order_facts(order(), "C_1182", NOW)
    assert facts["hours_since_delivered"] == 96.0
    assert facts["hours_past_promised_date"] == 24.0
    assert facts["refundable_inr"] == 2000.0
    assert facts["refunds"][0]["days_ago"] == 2.0


def test_undelivered_shipment_is_late_against_now():
    late = order(status="shipped", delivered_at=None,
                 shipments=[{"direction": "forward", "promised_by": "2026-09-23T12:00:00+00:00", "delivered_at": None}])
    assert order_facts(late, "C_1182", NOW)["hours_past_promised_date"] == 24.0


def test_another_customers_order_reveals_nothing():
    facts = order_facts(order(), "C_1999", NOW)
    assert facts == {"order_id": "A8842", "belongs_to_customer": False,
                     "note": "This order is on another customer's account."}


def test_a_refused_lookup_becomes_none(fake_tools):
    from agent.mcp_client import ToolOutcome
    from agent.nodes.retrieve import read

    tools = fake_tools({"get_order": ToolOutcome(error="1 validation error for get_orderArguments")})
    assert read(tools, "get_order", {"order_id": "A1"}) is None


def test_the_refund_timeline_for_the_payment_method_is_a_fact():
    assert order_facts(order(payment_method="upi"), "C_1182", NOW)["refund_reaches_customer"] == "in 1 to 3 business days"
    assert order_facts(order(payment_method="wallet"), "C_1182", NOW)["refund_reaches_customer"] == "within 24 hours"
    assert "refund_reaches_customer" not in order_facts(order(), "C_1182", NOW)
