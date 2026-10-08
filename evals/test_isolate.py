from datetime import datetime, timezone

import pytest

from agent.context import RunContext
from agent.guardrails.audit import MemoryAuditLog
from agent.providers import ChatModel
from agent.state import Plan, RetrievedPolicy
from data.index_policies import load_chunks
from evals.isolate import (ORACLE_NOTE, chosen_cases, complete, gap, generator_scores, labelled_plan, labelled_state,
                           oracle_policies, plan_ticket, planner_scores, ranking_scores, recorded_actions, run_ticket,
                           write_ticket)
from evals.run import load_cases

ANCHOR = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)
CASES = {c.case_id: c for c in load_cases("full")}
CHUNKS = load_chunks()
HIT = {"doc_id": "pol_refund_timelines", "title": "Refund Timelines", "chunk": "Refund Timelines\n## Overview\nRefunds go back the way they came.", "score": 0.7}
ORDER = {"order_id": "A3107", "customer_id": CASES["gold_001"].customer_id, "status": "shipped", "total_inr": 1799.0,
         "refunded_inr": 0.0, "placed_at": "2026-09-29T10:00:00+00:00", "delivered_at": None, "shipments": [], "refunds": [],
         "items": [], "shipping_city": "Pune", "payment_method": "upi"}


def context(model, tools):
    return RunContext(model=model, now=ANCHOR, tools=tools, audit=MemoryAuditLog())


@pytest.fixture
def tools(fake_tools):
    return fake_tools({"search_policy": [HIT], "get_order": ORDER, "get_customer_history": None})


def plan_reply(decision="answer", tool_name=None, tool_args=None, cites=("pol_lost_transit",)):
    return {"decision": decision, "tool_name": tool_name, "tool_args": tool_args, "cites": list(cites), "rationale": "r",
            "escalation_reason": "unsure" if decision == "escalate" else None}


def test_the_search_is_scored_at_every_depth_and_per_policy():
    rows = [{"case_id": "g1", "required": ["pol_a"], "ranked": ["pol_b#x", "pol_b#y", "pol_a#z"]},
            {"case_id": "g2", "required": ["pol_a"], "ranked": ["pol_a#z"]},
            {"case_id": "g3", "required": ["pol_esc"], "ranked": ["pol_a#z", "pol_b#x", "pol_b#y", "pol_c#x", "pol_c#y", "pol_esc#x"]}]
    s = ranking_scores(rows)

    assert s["recall_at"] == {"1": pytest.approx(1 / 3, abs=1e-4), "3": pytest.approx(2 / 3, abs=1e-4),
                              "5": pytest.approx(2 / 3, abs=1e-4), "8": 1.0, "10": 1.0}
    assert s["mrr"] == pytest.approx((1 / 3 + 1 + 1 / 6) / 3, abs=1e-4)
    assert s["precision_at_5"] == pytest.approx((1 / 3 + 1 + 0) / 3, abs=1e-4)
    assert s["per_policy_at_5"]["pol_esc"] == {"needed": 1, "found": 0, "recall": 0.0}
    assert s["misses_at_5"] == ["g3"] and s["pinned"] == []


def test_a_pinned_policy_counts_as_found_and_is_left_out_of_the_ranking():
    rows = [{"case_id": "g1", "required": ["pol_a"], "ranked": ["pol_b#x", "pol_a#z"], "pinned": ["pol_esc#x", "pol_esc#y"]},
            {"case_id": "g3", "required": ["pol_esc"], "ranked": ["pol_a#z", "pol_b#x"], "pinned": ["pol_esc#x", "pol_esc#y"]},
            {"case_id": "g4", "required": ["pol_a", "pol_esc"], "ranked": ["pol_b#x", "pol_c#x"], "pinned": ["pol_esc#x"]}]
    s = ranking_scores(rows)

    # g3 needs only the pinned policy, so it is found at every depth and the search is not scored on it.
    assert s["recall_at"]["1"] == pytest.approx(1 / 3, abs=1e-4) and s["recall_at"]["3"] == pytest.approx(2 / 3, abs=1e-4)
    assert s["per_policy_at_5"]["pol_esc"] == {"needed": 2, "found": 2, "recall": 1.0}
    assert s["misses_at_5"] == ["g4"] and s["pinned"] == ["pol_esc"]
    assert s["mrr"] == pytest.approx((1 / 2 + 0) / 2, abs=1e-4)
    assert s["precision_at_5"] == pytest.approx((1 / 2 + 0) / 2, abs=1e-4)


def test_the_labelled_state_is_what_a_perfect_classifier_leaves():
    case = CASES["gold_105"]
    state = labelled_state(case, ANCHOR)
    c = state["classification"]
    assert (c.intent, c.order_id, c.confidence) == ("cancellation", "A5037", 1.0)
    assert state["ticket_id"] == "gold_105" and state["raw_message"] == case.raw_message


def test_oracle_context_is_every_section_of_the_required_policies_and_nothing_else():
    found = oracle_policies(CASES["gold_001"], CHUNKS)
    assert {p.doc_id for p in found} == {"pol_lost_transit"}
    assert len(found) == sum(c.doc_id == "pol_lost_transit" for c in CHUNKS) > 1
    assert complete(CASES["gold_001"], found) and not complete(CASES["gold_001"], [RetrievedPolicy(**HIT)])
    assert oracle_policies(CASES["gold_067"], CHUNKS) == []


def test_only_tickets_a_step_would_really_see_are_given_to_it():
    cases = [CASES[i] for i in ("gold_001", "gold_013", "gold_067", "gold_105")]
    # gold_067 is out of scope and gold_105 claims to be staff, so both stop before the plan.
    assert [c.case_id for c, _ in chosen_cases("planner", cases, {}, ANCHOR)] == ["gold_001", "gold_013"]
    # Without a recorded run there is no action result to write from, so only the answer is written.
    assert [c.case_id for c, _ in chosen_cases("generator", cases, {}, ANCHOR)] == ["gold_001"]

    call = {"name": "issue_refund", "error": None, "result": {"refund_id": "RF-1"}, "authorized_by": "policy", "latency_ms": 5,
            "args": {"order_id": "A4236", "amount_inr": 1299, "reason_code": "lost_in_transit", "policy_doc_id": "pol_lost_transit"}}
    took_it = {"expected": {"tool_name": "issue_refund", "tool_args": CASES["gold_013"].expected.tool_args}, "tool_calls": [call]}
    refused = {**took_it, "tool_calls": [{**call, "error": "refused", "result": None}]}
    picked = chosen_cases("generator", cases, {"gold_013": took_it}, ANCHOR)
    assert [c.case_id for c, _ in picked] == ["gold_001", "gold_013"] and picked[1][1][0].name == "issue_refund"
    assert recorded_actions(refused, ANCHOR) is None and recorded_actions(None, ANCHOR) is None


def test_a_plan_is_compared_with_the_label_down_to_the_arguments(scripted, tools):
    case = CASES["gold_013"]
    state = {**labelled_state(case, ANCHOR), "policies": oracle_policies(case, CHUNKS), "order": None, "customer_history": None}
    right = plan_reply("act", "issue_refund", {**case.expected.tool_args, "idempotency_key": "x"})
    short = plan_reply("act", "issue_refund", {**case.expected.tool_args, "amount_inr": 999})
    model = scripted({"Plan": [right, short, plan_reply("escalate")]})
    ctx = context(model, tools)

    first, second, third = (plan_ticket(case, state, ctx) for _ in range(3))
    assert (first["right"], first["right_action"], first["cites_required"]) == (True, True, True)
    assert "idempotency_key" not in first["tool_args"]
    assert (second["right"], second["right_action"]) == (True, False)
    assert (third["right"], third["right_action"], third["decision"]) == (False, False, "escalate")
    # The planner is told the labelled intent and shown the oracle excerpts.
    assert "intent=refund_request" in model.client.structured_prompts[0] and "[doc_id: pol_lost_transit]" in model.client.structured_prompts[0]


def test_a_draft_is_written_from_one_context_and_scored_against_the_oracle(scripted, tools):
    case = CASES["gold_001"]
    truth = oracle_policies(case, CHUNKS)
    state = {**labelled_state(case, ANCHOR), "policies": [RetrievedPolicy(**HIT)], "order": {**ORDER, "belongs_to_customer": True},
             "customer_history": None, "plan": labelled_plan(case), "tool_calls": []}
    model = scripted({"ClaimCheck": [{"unsupported_claims": []}, {"unsupported_claims": ["It arrives tomorrow."]}, {"unsupported_claims": []}]},
                     texts=["Hi, your order is on its way.", "Hi, your order is on its way. It arrives tomorrow.",
                            "Hi, your refund has been processed."])
    ctx = context(model, tools)

    clean, doubted, claimed = (write_ticket(state, truth, ctx) for _ in range(3))
    assert clean["passed"] and clean["checked_by"] == "fake:scripted" and clean["sentences"] == 0
    assert not doubted["passed"] and doubted["unsupported"] == ["It arrives tomorrow."] and doubted["rule_faults"] == {}
    assert not claimed["passed"] and list(claimed["rule_faults"]) == ["claims_unperformed_action"]
    # The writer saw what it was given. The checker saw the oracle.
    assert "Refund Timelines" in model.client.texts_seen[0] and ORACLE_NOTE in model.client.texts_seen[0]
    assert "[doc_id: pol_lost_transit]" in model.client.structured_prompts[0]
    assert "[doc_id: pol_refund_timelines]" not in model.client.structured_prompts[0]


def test_both_contexts_share_one_search_and_a_crash_is_a_row_not_a_stop(scripted, tools):
    case = CASES["gold_001"]
    model = scripted({"Plan": [plan_reply("answer"), plan_reply("escalate")]})
    monkey_ctx = context(model, tools)
    import evals.isolate as isolate

    original = isolate.RunContext
    isolate.RunContext = lambda **kwargs: monkey_ctx
    try:
        rows = run_ticket("planner", case, ANCHOR, tools, CHUNKS, ["oracle", "retrieved"], None)
        crashed = run_ticket("planner", case, ANCHOR, tools, CHUNKS, ["oracle", "retrieved"], None)
    finally:
        isolate.RunContext = original

    assert [(r["context"], r["right"], r["policies_complete"]) for r in rows] == [("oracle", True, True), ("retrieved", False, False)]
    assert sum(name == "search_policy" for name, _ in tools.calls) == 2
    # The scripted model has no third plan, so the second ticket fails, and it fails as data.
    assert [r["context"] for r in crashed] == ["oracle", "retrieved"] and all("IndexError" in r["error"] for r in crashed)
    assert gap(rows + crashed, "right") == {"tickets_in_both": 1, "lost_to_retrieval": ["gold_001"], "better_with_retrieved": []}


def test_scores_keep_the_two_kinds_of_wrong_decision_apart():
    def row(case_id, label, decision, whole=True, action=None, reason=None):
        return {"case_id": case_id, "expected_decision": label, "decision": decision, "right": label == decision,
                "right_action": action, "policies_complete": whole, "escalation_reason": reason}

    s = planner_scores([row("g1", "answer", "answer"), row("g2", "act", "act", action=True), row("g3", "act", "escalate", action=False),
                        row("g4", "escalate", "answer", whole=False), row("g5", "act", "escalate", action=False, reason="act_without_tool")])
    assert (s["right"], s["accuracy"]) == (2, 0.4) and s["right_action"] == pytest.approx(1 / 3, abs=1e-4)
    assert s["sent_to_a_person_needlessly"] == ["g3", "g5"] and s["handled_what_needed_a_person"] == ["g4"]
    assert (s["accuracy_policies_complete"], s["accuracy_a_policy_missing"], s["unusable_plans"]) == (0.5, 0.0, 1)


def test_generator_scores_count_sentences_and_split_by_what_the_search_found():
    def row(case_id, passed, label="answer", whole=True, faults=(), unsupported=(), sentences=4, supported=4):
        return {"case_id": case_id, "expected_decision": label, "passed": passed, "policies_complete": whole,
                "rule_faults": {k: ["x"] for k in faults}, "unsupported": list(unsupported), "sentences": sentences,
                "supported": supported, "checker_fell_back": False}

    s = generator_scores([row("g1", True), row("g2", False, unsupported=["x"], supported=3),
                          row("g3", False, label="act", whole=False, faults=["wrong_refund_timeline"])])
    assert (s["passed"], s["pass_rate"]) == (1, pytest.approx(1 / 3, abs=1e-4))
    assert s["faults"] == {"unsupported_claims": 1, "wrong_refund_timeline": 1}
    assert (s["sentences_checked"], s["sentence_support"]) == (12, pytest.approx(11 / 12, abs=1e-4))
    assert (s["pass_rate_policies_complete"], s["pass_rate_a_policy_missing"]) == (0.5, 0.0)
    assert s["by_label"] == {"answer": {"tickets": 2, "passed": 1}, "act": {"tickets": 1, "passed": 0}} and s["failed"] == ["g2", "g3"]
