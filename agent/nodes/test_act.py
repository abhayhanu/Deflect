from agent.nodes.act import idempotency_key


def test_key_is_stable_and_ignores_argument_order():
    a = idempotency_key("gold_011", "issue_refund", {"order_id": "A8842", "amount_inr": 2400})
    b = idempotency_key("gold_011", "issue_refund", {"amount_inr": 2400, "order_id": "A8842"})
    assert a == b
    assert a.startswith("gold_011:issue_refund:") and len(a.rsplit(":", 1)[1]) == 16


def test_key_changes_with_anything_that_changes_the_action():
    base = idempotency_key("gold_011", "issue_refund", {"order_id": "A8842", "amount_inr": 2400})
    assert base != idempotency_key("gold_011", "issue_refund", {"order_id": "A8842", "amount_inr": 2399})
    assert base != idempotency_key("gold_012", "issue_refund", {"order_id": "A8842", "amount_inr": 2400})
    assert base != idempotency_key("gold_011", "cancel_order", {"order_id": "A8842", "amount_inr": 2400})
