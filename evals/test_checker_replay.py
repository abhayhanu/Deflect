from datetime import datetime, timezone

from agent.nodes.verify import checker_sources
from evals.checker_replay import chunks_by_section, flags_at, rebuild, rounds_to_replay, summarise, tally

ANCHOR = datetime(2026, 10, 1, 10, tzinfo=timezone.utc)


def record(rounds, **predicted):
    base = {"order_id": "A3391", "plan_decision": "answer", "cites": ["pol_refund_timelines"],
            "retrieved_sections": ["pol_refund_timelines#When the refund is issued", "pol_lost_transit#Overview", "pol_gone#Nowhere"]}
    return {"case_id": "gold_003", "predicted": {**base, **predicted}, "checker_rounds": rounds,
            "tool_calls": [{"name": "issue_refund", "args": {"order_id": "A3391"}, "result": {"refund_id": "RF-1"}, "error": None,
                            "latency_ms": 12, "authorized_by": "policy", "approver_id": None},
                           {"name": "cancel_order", "args": {"order_id": "A3391"}, "result": None, "error": "already_shipped",
                            "latency_ms": 9, "authorized_by": "policy", "approver_id": None}]}


def checked(failed, draft="Your refund is on its way."):
    return {"verdict": "retry" if failed else "pass", "failed_checks": failed, "unsupported_claims": ["x"] if failed else [], "draft": draft}


def test_only_rounds_where_a_model_was_asked_are_replayed():
    results = {"cases": [record([checked(["template_slot"]), checked(["unsupported_claims"]), checked([])]),
                         {"case_id": "gold_004", "predicted": {}, "checker_rounds": []}]}
    assert [(r["case_id"], n) for r, n, _ in rounds_to_replay(results)] == [("gold_003", 1), ("gold_003", 2)]
    assert rounds_to_replay(results, only=["gold_004"]) == []


def test_the_checker_reads_the_same_sources_the_run_recorded():
    chunks = chunks_by_section()
    state = rebuild(record([]), "Your refund is on its way.", {"order_id": "A3391"}, chunks, ANCHOR)
    sources = checker_sources(state)

    assert [p["doc_id"] for p in sources["policy_excerpts"]] == ["pol_refund_timelines"]
    assert "## When the refund is issued" in sources["policy_excerpts"][0]["text"]
    assert sources["order_record"] == {"order_id": "A3391"}
    # A refused action is not something the reply may lean on.
    assert sources["actions_taken"] == [{"action": "issue_refund", "arguments": {"order_id": "A3391"},
                                         "result": {"refund_id": "RF-1"}}]
    assert sources["reply"] == "Your refund is on its way."


def row(recorded, doubt=None, flagged=None, n=0, error=None):
    return {"case_id": "c", "round": n, "recorded_flagged": recorded, "sentence_doubt": doubt or {}, "flagged": flagged or [],
            "error": error, "latency_ms": 40, "cost_inr": 0.01}


def test_a_threshold_is_applied_to_the_recorded_probabilities():
    doubted = row(True, {"It arrives tomorrow.": 0.6})
    assert flags_at(doubted, 0.5) and not flags_at(doubted, 0.7)
    # A chat model gives no probabilities, so its own verdict stands at every threshold.
    assert flags_at(row(False, flagged=["a quote"]), 0.9)


def test_the_tally_separates_the_two_kinds_of_disagreement():
    rows = [row(True, {"a": 0.9}), row(True, {"a": 0.1}), row(False, {"a": 0.8}, n=1), row(False, {"a": 0.2}, n=1),
            row(True, error="DeciderError: down")]
    numbers = tally(rows, 0.5)
    assert numbers == {"rounds": 4, "recorded_flagged": 2, "replay_flagged": 2, "both_pass": 1, "both_flag": 1,
                       "only_recorded_flagged": 1, "only_replay_flagged": 1, "agreement": 0.5}
    summary = summarise(rows)
    assert summary["errors"] == 1 and summary["first_drafts"]["rounds"] == 2
    assert set(summary["by_threshold"]) == {"0.3", "0.5", "0.7"} and summary["cost_inr"] == 0.04


def test_a_new_record_keeps_the_order_the_agent_read():
    from agent.context import RunContext
    from evals.run import load_cases, outcome

    order = {"order_id": "A3107", "status": "shipped", "shipping_city": "Bengaluru"}
    kept = outcome(load_cases("full")[0], {"order": order}, RunContext(), 10, None, [])
    assert kept["order"] == order
    assert rounds_to_replay({"cases": [kept]}) == []

