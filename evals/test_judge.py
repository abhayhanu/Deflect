import csv
import json

import pytest

from agent.providers import ChatModel
from evals import judge
from evals.judge_agreement import agreement, pick, read_grades, sheet
from evals.metrics import compute

REFERENCE = "Confirms the Rs 1,899 refund and says it reaches UPI in 1 to 3 business days."


def record(case_id="gold_086", reason="acted", reply="Hi, your refund of Rs 1,899 is issued. Write to me at asha@example.com."):
    return {
        "case_id": case_id, "tags": ["straightforward", "smoke"], "error": None,
        "expected": {"intent": "refund_request", "decision": "act", "must_escalate": False, "required_policy_ids": [],
                     "tool_name": "issue_refund", "tool_args": {"amount_inr": 1899}, "reference_reply": REFERENCE},
        "predicted": {"intent": "refund_request", "decision": "act", "escalated": reason.startswith("escalated"),
                      "terminal_reason": reason, "cites": ["pol_damaged_goods"], "retrieved_ids": ["pol_damaged_goods"]},
        "tool_calls": [{"name": "issue_refund", "error": None,
                        "args": {"order_id": "A9714", "amount_inr": 1899, "idempotency_key": "gold_086:issue_refund:abc"},
                        "result": {"refund_id": "RF-A9714-1", "payment_method": "upi"}}],
        "reply": reply, "cost_inr": 0.0, "latency_ms": 1, "parse_failures": 0, "structured_calls": 2,
    }


CANCELLED = {"order_id": "A2617", "status": "cancelled", "refunded_inr": 1599.0, "shipping_city": "Lucknow",
             "refunds": [{"refund_id": "RF-A2617-1", "amount_inr": 1599.0, "status": "processed"}]}


def test_the_judge_is_shown_the_order_so_a_true_statement_is_not_called_invented():
    """v6: a reply that said a cancelled order was cancelled scored 1.75 for inventing it."""
    answered = {**record("gold_095", reason="answered", reply="Your order A2617 was cancelled and Rs 1,599 refunded."),
                "tool_calls": [], "order": CANCELLED}
    prompt = "\n".join(m.content for m in judge.judge_prompt(answered, "where is my order A2617?"))
    shown = prompt.split("Order record from our database:")[1].split("Action taken:")[0]
    assert '"status": "cancelled"' in shown and "RF-A2617-1" in shown
    assert "the order record from our" in judge.RUBRIC and "applied" in judge.RUBRIC and judge.RUBRIC_VERSION == 2

    nothing = "\n".join(m.content for m in judge.judge_prompt({**answered, "order": None}, "do you sell socks?"))
    assert "No order was found for this ticket." in nothing


def test_a_new_rubric_judges_again_and_keeps_the_old_scores(scripted, monkeypatch):
    monkeypatch.setattr(judge, "golden_messages", lambda: {"gold_086": "cracked plates", "gold_001": "where is it"})
    old = {"accuracy": 1, "completeness": 1, "tone": 3, "restraint": 1, "mean": 1.5, "notes": "hallucinated"}
    fresh = {"accuracy": 4, "completeness": 4, "tone": 4, "restraint": 4, "mean": 4.0, "notes": "ok", "rubric": 2}
    results = {"meta": {"provider": "ollama", "model": "qwen2.5:7b"}, "judge": {"provider": "fake", "cost_inr": 9.0},
               "cases": [{**record(), "judge": dict(old)}, {**record("gold_001", reason="answered"), "judge": dict(fresh)}]}
    model = scripted({"JudgeScore": [{"accuracy": 5, "completeness": 5, "tone": 5, "restraint": 5, "notes": "true to the order"}]})

    summary = judge.judge_results(results, model, workers=1)
    first, second = results["cases"]
    assert first["judge"]["mean"] == 5.0 and first["judge"]["rubric"] == 2 and first["judge_earlier"] == [old]
    assert second["judge"] == fresh and "judge_earlier" not in second
    assert model.client.calls == ["JudgeScore"]
    # The cost of the old rubric's run is not carried into the new one.
    assert summary["rubric"] == 2 and summary["cost_inr"] < 9.0


def test_scores_from_two_rubrics_are_never_averaged_together():
    old = {"accuracy": 1, "completeness": 1, "tone": 1, "restraint": 1, "mean": 1.0, "notes": ""}
    new = {"accuracy": 5, "completeness": 5, "tone": 5, "restraint": 5, "mean": 5.0, "notes": "", "rubric": 2}
    only_old = compute([{**record(), "judge": old}, {**record("gold_001"), "judge": old}])
    assert only_old["judged_replies"] == 2 and only_old["reply_quality"] == 1.0
    mixed = compute([{**record(), "judge": old}, {**record("gold_001"), "judge": new}])
    assert mixed["judged_replies"] == 1 and mixed["reply_quality"] == 5.0


def test_a_version_gets_one_quality_row_per_rubric(tmp_path):
    from evals.report import QUALITY_START, append_row, build_quality_row, quality_key

    page = tmp_path / "EVALS.md"
    page.write_text(f"{QUALITY_START}\n\n| Version | Judge |\n| --- | --- |\n", encoding="utf-8")
    results = {"metrics": {"judged_replies": 34, "judge_means": {}, "reply_quality": 4.22},
               "judge": {"provider": "gemini", "model": "gemini-3.8-flash", "cost_inr": 8.16}}
    append_row(page, quality_key("v6", results), build_quality_row("v6", results), QUALITY_START)
    results["judge"]["rubric"] = 2
    append_row(page, quality_key("v6", results), build_quality_row("v6", results), QUALITY_START)
    with pytest.raises(SystemExit, match="already has a row"):
        append_row(page, quality_key("v6", results), build_quality_row("v6", results), QUALITY_START)
    rows = [line for line in page.read_text(encoding="utf-8").splitlines() if line.startswith("| v6 |")]
    assert [r.split(" | ")[1] for r in rows] == ["gemini gemini-3.8-flash", "gemini gemini-3.8-flash, rubric 2"]


def test_orders_already_in_the_file_need_no_database(monkeypatch):
    from evals import sources

    monkeypatch.setattr(sources, "connect", lambda: (_ for _ in ()).throw(AssertionError("the database was opened")))
    assert sources.attach_orders({"meta": {}, "cases": [{**record(), "order": None}, {**record(), "order": CANCELLED}]}) == 0


def test_the_judge_never_sees_the_labels_or_the_reference_reply():
    prompt = "\n".join(m.content for m in judge.judge_prompt(record(), "Plates cracked, mail asha@example.com"))
    assert REFERENCE not in prompt and "1 to 3 business days" not in prompt.split("Reply:")[1]
    assert "must_escalate" not in prompt and "expected" not in prompt
    assert "idempotency" not in prompt and "RF-A9714-1" in prompt
    assert "asha@example.com" not in prompt and "<EMAIL_1>" in prompt
    assert "[doc_id: pol_damaged_goods]" in prompt
    assert prompt.startswith("You are grading a customer support reply.")


def test_escalations_and_crashes_are_skipped():
    assert judge.should_judge(record(reason="escalated_by_plan")).startswith("escalation")
    assert judge.should_judge({**record(), "error": "boom"}) == "the run crashed"
    assert judge.should_judge(record()) is None


def test_scores_land_in_the_results_and_the_metrics(scripted, monkeypatch):
    monkeypatch.setattr(judge, "golden_messages", lambda: {"gold_086": "cracked plates", "gold_001": "where is it"})
    scores = [{"accuracy": 5, "completeness": 4, "tone": 4, "restraint": 5, "notes": "fine"}, "not json"]
    model = scripted({"JudgeScore": scores + ["still not json", "nope"]})
    results = {"meta": {"provider": "ollama", "model": "qwen2.5:7b"},
               "cases": [record(), record("gold_001", reason="answered"), record("gold_002", reason="escalated_by_plan")]}

    summary = judge.judge_results(results, model, workers=1)
    first, second, third = (c["judge"] for c in results["cases"])
    assert first["mean"] == 4.5
    assert "failed" in second and "skipped" in third
    m = results["metrics"]
    assert m["judged_replies"] == 1 and m["reply_quality"] == 4.5
    assert m["judge_means"] == {"accuracy": 5.0, "completeness": 4.0, "tone": 4.0, "restraint": 5.0}
    assert m["by_category"]["straightforward"]["reply_quality"] == 4.5
    assert summary["same_model_as_agent"] is False


def test_a_crashed_judge_call_keeps_the_other_scores(monkeypatch):
    class Broken:
        def with_structured_output(self, *a, **k):
            raise TimeoutError("rate limited")

    values, _ = judge.judge_one(ChatModel("openai", "x", Broken()), record(), "msg")
    assert values["failed"].startswith("TimeoutError")


def test_the_grading_sheet_is_blind_and_agreement_is_counted(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.judge_agreement.golden_messages", lambda: {f"gold_{i:03d}": "msg" for i in range(40)})
    records = [record(f"gold_{i:03d}", reason="acted" if i % 3 else "answered") for i in range(30)]
    for r in records:
        r["judge"] = {"accuracy": 5, "completeness": 3, "tone": 4, "restraint": 5, "mean": 4.25, "notes": "secret note"}
    chosen = pick(records, 20)
    assert len(chosen) == 20 and chosen == pick(records, 20)
    assert {r["predicted"]["terminal_reason"] for r in chosen} == {"acted", "answered"}
    chosen[0]["order"] = CANCELLED
    text = sheet(chosen, "results.json")
    assert "secret note" not in text and "4.25" not in text and "asha@example.com" not in text
    # The grader sees what the judge sees, the order included.
    assert "**Order record**" in text and '"status": "cancelled"' in text and "the order record from our" in text

    grades = tmp_path / "grades.csv"
    with grades.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "accuracy", "completeness", "tone", "restraint", "notes"])
        writer.writerow([chosen[0]["case_id"], 5, 3, 4, 5, ""])
        writer.writerow([chosen[1]["case_id"], 4, 5, 4, 2, ""])
        writer.writerow([chosen[2]["case_id"], "", "", "", "", "not graded yet"])
    human = read_grades(grades)
    assert len(human) == 2
    result = agreement(human, {r["case_id"]: r["judge"] for r in chosen})
    assert result["replies"] == 2
    assert result["exact"] == 5 / 8 and result["within_one"] == 6 / 8
    assert result["by_dimension"]["restraint"] == {"exact": 0.5, "within_one": 0.5, "judge_minus_human": 1.5}

    from evals.judge_agreement import file_names, furthest_apart

    [line] = furthest_apart(human, {r["case_id"]: r["judge"] for r in chosen})
    assert line.startswith(f"{chosen[1]['case_id']}: completeness you 5 judge 3, restraint you 2 judge 5.") and "secret note" in line
    assert file_names(None) == ("grading_sheet.md", "human_grades.csv")
    assert file_names(2) == ("grading_sheet_2.md", "human_grades_2.csv")


def test_grades_that_exist_are_never_written_over(tmp_path, monkeypatch, capsys):
    from evals import judge_agreement

    (tmp_path / "human_grades.csv").write_text("case_id,accuracy,completeness,tone,restraint,notes\ngold_001,5,5,4,5,ok\n",
                                               encoding="utf-8")
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"meta": {}, "cases": []}), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["judge_agreement", "sheet", str(results), "--out", str(tmp_path)])
    assert judge_agreement.main() == 2
    assert "never written over" in capsys.readouterr().err
    assert "gold_001,5,5,4,5,ok" in (tmp_path / "human_grades.csv").read_text(encoding="utf-8")


def test_reply_quality_is_none_until_the_judge_runs():
    assert compute([record()])["reply_quality"] is None
