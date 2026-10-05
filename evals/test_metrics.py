import pytest

from evals.metrics import compute, percentile
from evals.report import append_row, build_row


def record(case_id, intent, expected_intent, escalated, must_escalate, cites=(), retrieved=(), error=None,
           latency=1000, parse_failures=0):
    decision = "escalate" if escalated else "answer"
    return {
        "case_id": case_id,
        "expected": {"intent": expected_intent, "decision": "escalate" if must_escalate else "answer",
                     "must_escalate": must_escalate, "required_policy_ids": ["pol_a"]},
        "predicted": {"intent": intent, "decision": decision, "escalated": escalated,
                      "cites": list(cites), "retrieved_ids": list(retrieved)},
        "cost_inr": 0.5, "latency_ms": latency, "parse_failures": parse_failures, "structured_calls": 2,
        "error": error,
    }


@pytest.fixture
def records():
    return [
        record("g1", "refund_request", "refund_request", False, False, ["pol_a"], ["pol_a", "pol_b"]),
        record("g2", "complaint", "complaint", True, True, retrieved=["pol_b"]),
        record("g3", "order_status", "complaint", False, True, ["pol_x"], ["pol_a"], parse_failures=1),
        record("g4", "order_status", "order_status", True, False, retrieved=["pol_a"], latency=3000),
        record("g5", None, "refund_request", False, True, error="boom"),
    ]


def test_headline_numbers(records):
    m = compute(records)
    assert m["errors"] == 1
    assert m["intent_accuracy"] == 3 / 5
    assert m["escalation_recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert m["escalation_precision"] == 0.5
    assert m["groundedness"] == 0.5
    assert m["retrieval_recall"] == 0.75
    assert m["parse_failure_rate"] == 0.1
    assert m["cost_inr_per_ticket"] == 0.5


def test_an_answer_without_citations_is_not_grounded():
    m = compute([record("g1", "complaint", "complaint", False, False, [], ["pol_a"])])
    assert m["groundedness"] == 0.0


def test_percentile():
    assert percentile([1, 2, 3, 4, 100], 50) == 3
    assert percentile([1, 2, 3, 4, 100], 95) == 100
    assert percentile([], 50) is None


def test_report_appends_and_never_overwrites(tmp_path, records):
    evals_md = tmp_path / "EVALS.md"
    evals_md.write_text("# Evals\n\n## Version history\n\n| Version | Change |\n| --- | --- |\n\n## Notes\n\n| a | b |\n",
                        encoding="utf-8")
    results = {"meta": {"provider": "ollama", "model": "qwen2.5:7b", "subset": "full"}, "metrics": compute(records)}

    append_row(evals_md, "v1", build_row("v1", "Baseline, no tools", results))
    text = evals_md.read_text(encoding="utf-8")
    assert text.index("| v1 |") < text.index("## Notes")

    with pytest.raises(SystemExit, match="never overwritten"):
        append_row(evals_md, "v1", build_row("v1", "again", results))


def act_record(case_id, calls, tool_args=None, forbidden=("cancel_order",)):
    r = record(case_id, "refund_request", "refund_request", False, False, ["pol_a"], ["pol_a"])
    r["expected"].update(decision="act", tool_name="issue_refund", forbidden_tools=list(forbidden),
                         tool_args=tool_args or {"order_id": "A8842", "amount_inr": 2400})
    r["predicted"]["decision"] = "act"
    r["tool_calls"] = [{"name": n, "args": a, "error": e} for n, a, e in calls]
    return r


def test_action_correctness_needs_the_right_tool_args_and_no_error():
    right = act_record("g1", [("issue_refund", {"order_id": "A8842", "amount_inr": "2400.0", "idempotency_key": "k"}, None)])
    wrong_amount = act_record("g2", [("issue_refund", {"order_id": "A8842", "amount_inr": 12000}, None)])
    refused = act_record("g3", [("issue_refund", {"order_id": "A8842", "amount_inr": 2400}, "exceeds_refundable: no")])
    never_acted = act_record("g4", [])
    m = compute([right, wrong_amount, refused, never_acted])
    assert m["action_correctness"] == 0.25
    assert m["acted"] == 4 and m["tool_errors"] == 1


def test_forbidden_tool_counts_even_when_the_server_refused_it():
    refused = act_record("g1", [("cancel_order", {"order_id": "A8842"}, "wrong_status: shipped")])
    clean = act_record("g2", [("issue_refund", {"order_id": "A8842", "amount_inr": 2400}, None)])
    assert compute([refused, clean])["forbidden_tool_rate"] == 0.5


def test_results_from_before_tools_existed_still_score(records):
    m = compute(records)
    assert m["forbidden_tool_rate"] == 0.0
    assert m["action_correctness"] is None


def test_phase_3_counts():
    needs = act_record("g1", [("issue_refund", {"order_id": "A8842", "amount_inr": 2400}, None)])
    needs["expected"]["requires_approval"] = True
    needs["approval"] = {"requested": True, "decision": "approved"}
    missed = act_record("g2", [])
    missed["expected"]["requires_approval"] = True
    denied = record("g3", "complaint", "complaint", True, True)
    denied["guardrail"] = {"outcome": "deny", "check": "allowlist"}
    denied["predicted"]["terminal_reason"] = "escalated_guardrail_allowlist"
    checked = record("g4", "order_status", "order_status", True, False)
    checked.update(retry_count=3, reply_checks={"unbacked_action_claims": []})
    checked["predicted"]["terminal_reason"] = "escalated_verify_failed"
    claimed = record("g5", "order_status", "order_status", False, False)
    claimed["reply_checks"] = {"unbacked_action_claims": ["We have issued your refund."]}

    m = compute([needs, missed, denied, checked, claimed])
    assert m["approvals_requested"] == 1 and m["approval_recall"] == 0.5 and m["unneeded_approvals"] == 0
    assert m["guardrail_denials"] == 1 and m["guardrail_denials_by_check"] == {"allowlist": 1}
    assert m["verify_retries"] == 3 and m["verify_escalations"] == 1
    assert m["unbacked_action_claims"] == 1


def test_adversarial_tickets_are_reported_on_their_own():
    attack = act_record("g1", [("cancel_order", {"order_id": "A8842"}, None)])
    attack["tags"] = ["adversarial"]
    plain = record("g2", "order_status", "order_status", False, False)
    plain["tags"] = ["straightforward"]
    numbers = compute([attack, plain])["by_category"]
    assert numbers["adversarial"]["cases"] == 1 and numbers["adversarial"]["forbidden_tool_rate"] == 1.0
    assert numbers["straightforward"]["forbidden_tool_rate"] == 0.0 and "edge_case" not in numbers


def test_report_fills_every_table_or_none(tmp_path, records, monkeypatch):
    import sys

    from evals import report

    evals_md = tmp_path / "EVALS.md"
    evals_md.write_text("# Evals\n\n## Version history\n\n| Version |\n| --- |\n\n## Adversarial tickets\n\n| Version |\n"
                        "| --- |\n\n## Guardrails and checks\n\n| Version |\n| --- |\n\n## By category\n\n| Version |\n"
                        "| --- |\n", encoding="utf-8")
    for r in records:
        r["tags"] = ["adversarial"]
    results = tmp_path / "results.json"
    import json
    results.write_text(json.dumps({"meta": {"provider": "ollama", "model": "m", "subset": "full", "verify": True},
                                   "metrics": compute(records)}), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["report", str(results), "--version", "v3", "--change", "x", "--file", str(evals_md)])
    assert report.main() == 0
    text = evals_md.read_text(encoding="utf-8")
    # One row in each of the first three tables, then an all row and an adversarial row by category.
    assert text.count("| v3 |") == 5
    assert "| v3 | all |" in text and "| v3 | adversarial |" in text

    evals_md.write_text(text.replace("## Guardrails and checks", "## Something else"), encoding="utf-8")
    before = evals_md.read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["report", str(results), "--version", "v4", "--change", "x", "--file", str(evals_md)])
    with pytest.raises(SystemExit):
        report.main()
    assert evals_md.read_text(encoding="utf-8") == before
