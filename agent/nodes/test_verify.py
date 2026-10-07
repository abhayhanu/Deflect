from datetime import datetime, timezone

import pytest

from agent.guardrails.policy import REFUND_ARRIVAL
from agent.nodes.verify import TEMPLATE_SLOT, action_problems, in_reply, unbacked_claims, wrong_timelines
from agent.state import Plan, ToolCall

AT = datetime(2026, 9, 25, tzinfo=timezone.utc)


def call(name, result=None, error=None, **args):
    return ToolCall(name=name, args=args, result=result or {}, error=error, authorized_by="policy", latency_ms=1, called_at=AT)


def state(order=None, calls=(), decision="answer", tool=None, cites=("pol_lost_transit",)):
    plan = Plan(decision=decision, tool_name=tool, cites=list(cites), rationale="x")
    base = {"order_id": "A1", "belongs_to_customer": True, "status": "delivered", "refunds": [], "refunded_inr": 0, "shipments": []}
    return {"plan": plan, "order": {**base, **(order or {})}, "tool_calls": list(calls)}


# Replies from the v2 run that told customers something happened when nothing did.
@pytest.mark.parametrize("case, reply", [
    ("gold_021", "Hi, We apologize for the wrong size you received. We will process a refund of Rs 2,799 for order A5012."),
    ("gold_045", "Hi, Your order A7483 has been cancelled successfully. The items will no longer be shipped."),
    ("gold_082", "According to our policy, as the order value is below Rs 5,000, we will issue a full refund of Rs 5,049."),
    ("gold_085", "However, we understand your concern and are issuing a full refund of Rs 749 immediately."),
    ("gold_098", "The refund will be processed and credited to your original payment method within 5 to 7 business days."),
    ("gold_060", "We do not offer direct exchanges. We will escalate this to our support team for further guidance."),
])
def test_v2_replies_that_claimed_actions_never_taken(case, reply):
    assert unbacked_claims(reply, state(order={"status": "placed"})), case


@pytest.mark.parametrize("reply, facts, calls", [
    ("Your order A2617 has been cancelled and fully refunded.", {"status": "cancelled", "refunded_inr": 1599}, []),
    ("Your refund of Rs 2890.0 for order A5270 has been processed.", {"refunds": [{"refund_id": "RF-1"}]}, []),
    ("The address for order A6472 has been updated to <ADDRESS_1>.", {"status": "placed"},
     [call("update_shipping_address", order_id="A1")]),
    ("Your order has been cancelled. The refund of Rs 899 has been processed.", {"status": "placed"},
     [call("cancel_order", result={"refund": {"refund_id": "RF-2"}}, order_id="A1")]),
    ("Once the item passes inspection, the refund will be issued to your card.", {}, []),
    ("If it still has not arrived, we can escalate this to our support team.", {}, []),
    ("Refunds for UPI payments take 1 to 3 business days.", {}, []),
])
def test_true_or_conditional_statements_pass(reply, facts, calls):
    assert unbacked_claims(reply, state(order=facts, calls=calls)) == []


def test_a_failed_action_backs_nothing():
    failed = call("issue_refund", error="exceeds_refundable: no", order_id="A1")
    assert unbacked_claims("We have processed your refund.", state(calls=[failed]))


def test_template_slots_are_found():
    reply = "Your order, placed on [order_date], will arrive by [promised_delivery_date]."
    assert TEMPLATE_SLOT.findall(reply) == ["[order_date]", "[promised_delivery_date]"]
    assert TEMPLATE_SLOT.findall("Order A1 costs Rs 2,400.") == []


def test_action_must_match_the_plan_and_cite_its_policy():
    ok = call("issue_refund", order_id="A1", policy_doc_id="pol_lost_transit")
    assert action_problems(state(calls=[ok], decision="act", tool="issue_refund")) == []
    assert action_problems(state(calls=[ok], decision="act", tool="cancel_order")) == ["action_mismatch"]
    uncited = call("issue_refund", order_id="A1", policy_doc_id="pol_damaged_goods")
    assert action_problems(state(calls=[uncited], decision="act", tool="issue_refund")) == ["refund_not_cited"]


def test_only_quotes_really_in_the_reply_count():
    reply = "Hi, your refund of Rs 1,899 reaches your UPI account in 3 to 5 business days."
    assert in_reply("3 to 5 business days", reply)
    assert in_reply("refund of Rs 1,899 reaches your UPI account", reply)
    assert not in_reply("we will send a free replacement", reply)
    assert not in_reply("", reply)


# Sentences from the v4 run: retrieval never returned the timelines policy, and the checker
# model passed both.
@pytest.mark.parametrize("case, method, reply", [
    ("gold_086", "upi", "Please allow 3-5 business days for the refund to reflect in your UPI account."),
    ("gold_090", "card", "Please allow 3-5 business days for the refund to reflect on your card."),
    ("gold_046", "netbanking", "You can expect the refund to reflect in your account within 7-10 business days."),
    ("wallet", "wallet", "The money will be credited to your wallet within 3 days."),
])
def test_a_refund_timeline_that_does_not_match_the_payment_method_is_caught(case, method, reply):
    found = wrong_timelines(reply, state(order={"payment_method": method}))
    assert len(found) == 1, case
    assert REFUND_ARRIVAL[method].says in found[0]


@pytest.mark.parametrize("method, reply", [
    ("upi", "Your refund of Rs 1,899 reaches your UPI account in 1 to 3 business days."),
    ("card", "You can expect it to reflect on your card within the next 5 to 7 business days."),
    ("wallet", "The refund will be credited to your wallet within 24 hours."),
    ("card", "If you have not received the refund within 9 business days, write to us."),
    ("card", "Since you reported it within 72 hours, we can issue a refund."),
    ("card", "Once the item is picked up, the refund is issued within 2 business days."),
    ("upi", "Your order will be delivered in 3 to 5 days."),
])
def test_right_conditional_or_unrelated_time_frames_pass(method, reply):
    assert wrong_timelines(reply, state(order={"payment_method": method})) == []


def test_the_refund_result_decides_the_method_and_an_unknown_one_checks_nothing():
    refund = call("issue_refund", result={"refund_id": "RF-1", "payment_method": "upi"}, order_id="A1")
    reply = "It reaches you in 5 to 7 business days."
    assert wrong_timelines("The refund reaches you in 5 to 7 business days.", state(order={"payment_method": "card"}, calls=[refund]))
    assert wrong_timelines(reply, state(order={"belongs_to_customer": False, "payment_method": "card"})) == []
    assert wrong_timelines("The refund reaches you in 5 to 7 business days.", state(order={"payment_method": "crypto"})) == []
