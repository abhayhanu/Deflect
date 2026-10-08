import pytest

from agent.providers import ChatModel
from evals.classifier_replay import against_recorded, at_floor, classify_case, reachable, summarise
from evals.metrics import compute
from evals.report import build_row, model_cell
from evals.run import load_cases
from evals.test_metrics import record

CASES = {c.case_id: c for c in load_cases("full")}


def row(case_id, intent, confidence=0.9, fell_back=False, error=None):
    case = CASES[case_id]
    return {"case_id": case_id, "category": case.category, "expected_intent": case.expected.intent,
            "expected_decision": case.expected.decision, "must_escalate": case.expected.must_escalate,
            "intent": intent, "confidence": confidence, "fell_back": fell_back, "error": error, "latency_ms": 300, "cost_inr": 0.01}


def recorded(**intents):
    return {"cases": [{"case_id": case_id, "predicted": {"intent": intent, "terminal_reason": "escalated_by_plan"}}
                      for case_id, intent in intents.items()]}


def test_a_ticket_is_redacted_then_classified_and_scored_against_its_label(scripted):
    case = CASES["gold_001"]
    answer = {"intent": case.expected.intent, "confidence": 0.9, "urgency": "medium", "sentiment": "neutral",
              "order_id": None, "reasoning": "x"}
    model = scripted({"Classification": [answer]})
    got = classify_case(model, case)

    assert (got["intent"], got["classified_by"], got["fell_back"], got["error"]) == (case.expected.intent, "fake:scripted", False, None)
    assert got["order_id"] == case.order_id and got["intent_probabilities"] == {}
    assert "Ticket via" in model.client.structured_prompts[0]


def test_a_classifier_that_cannot_run_counts_as_wrong():
    got = classify_case(ChatModel("fake", "broken", None), CASES["gold_001"])
    assert got["intent"] is None and "AttributeError" in got["error"]
    assert summarise([got])["intent_accuracy"] == 0.0 and summarise([got])["errors"] == 1


def test_the_floor_counts_what_would_be_lost_to_a_person():
    rows = [row("gold_001", CASES["gold_001"].expected.intent, 0.55), row("gold_067", "product_question", 0.5),
            row("gold_002", CASES["gold_002"].expected.intent, 0.95)]
    assert at_floor(rows, 0.6) == {"under": 2, "under_and_wrong": 1, "under_and_must_escalate": 1, "under_and_should_be_handled": 1}
    assert at_floor(rows, 0.4)["under"] == 0
    summary = summarise(rows)
    assert summary["intent_accuracy"] == pytest.approx(2 / 3, abs=1e-4) and summary["cost_inr"] == 0.03
    assert summary["confusions"] == [["out_of_scope read as product_question", 1]]


def test_a_stand_in_is_counted_apart_and_left_out_of_the_latency():
    rows = [row("gold_001", "refund_request"), row("gold_002", "refund_request", fell_back=True)]
    rows[1]["latency_ms"] = 90_000
    summary = summarise(rows)
    assert summary["fell_back"] == 1 and summary["latency_ms_p95"] == 300


def test_fixed_and_broken_are_read_against_the_recorded_run():
    rows = [row("gold_105", "cancellation"), row("gold_001", "complaint"), row("gold_002", CASES["gold_002"].expected.intent)]
    was = recorded(gold_105="refund_request", gold_001=CASES["gold_001"].expected.intent, gold_002=CASES["gold_002"].expected.intent)
    compared = against_recorded(rows, CASES, was)

    assert compared["tickets"] == 3 and compared["same_intent"] == 1
    assert compared["recorded_accuracy"] == pytest.approx(2 / 3, abs=1e-4)
    assert [n["case_id"] for n in compared["fixed"]] == ["gold_105"]
    assert [n["case_id"] for n in compared["broken"]] == ["gold_001"]
    assert compared["fixed"][0]["recorded_outcome"] == "escalated_by_plan"


def test_the_allowlist_is_read_together_with_the_escalation_signals():
    # gold_105 claims to be staff, so the guardrail refuses every change whatever the intent.
    assert reachable(CASES["gold_105"], "cancellation") == set()
    # gold_101 wants a shipped order cancelled. As a cancellation only the policy rules stop it.
    assert reachable(CASES["gold_101"], "return_request") == set()
    assert reachable(CASES["gold_101"], "cancellation") == {"cancel_order", "issue_refund"}

    compared = against_recorded([row("gold_101", "cancellation"), row("gold_115", "order_status")], CASES,
                                recorded(gold_101="return_request", gold_115="address_change"))
    assert [(n["case_id"], n["tools"]) for n in compared["allowlist_opened"]] == [("gold_101", ["cancel_order", "issue_refund"])]
    assert [(n["case_id"], n["tools"]) for n in compared["allowlist_closed"]] == [("gold_115", ["update_shipping_address"])]


def test_a_run_counts_stand_ins_and_tickets_stopped_by_the_floor():
    records = [record("g1", "refund_request", "refund_request", False, False, ["pol_a"], ["pol_a"]),
               record("g2", "complaint", "complaint", True, True), record("g3", "complaint", "complaint", True, False)]
    records[1]["predicted"]["classifier_fell_back"] = True
    records[2]["predicted"]["terminal_reason"] = "escalated_low_confidence"
    m = compute(records)
    assert (m["classifier_fallbacks"], m["low_confidence_escalations"], m["signal_escalations"]) == (1, 1, 0)
    records[1]["predicted"]["terminal_reason"] = "escalated_message_signal"
    assert compute(records)["signal_escalations"] == 1


def test_a_version_names_its_classifier_only_when_it_is_not_the_agents_model():
    meta = {"provider": "ollama", "model": "qwen2.5:7b", "subset": "full"}
    assert model_cell(meta) == model_cell({**meta, "classifier": "ollama:qwen2.5:7b"}) == "ollama qwen2.5:7b"
    assert model_cell({**meta, "classifier": "jev:jev-latest"}) == "ollama qwen2.5:7b, classified by jev jev-latest"
    results = {"meta": {**meta, "classifier": "jev:jev-latest"},
               "metrics": compute([record("g1", "complaint", "complaint", True, True)])}
    assert "| ollama qwen2.5:7b, classified by jev jev-latest |" in build_row("v7", "+ Jev as the classifier", results)
