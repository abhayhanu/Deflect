import json
import sys

import pytest

from evals import compare, gate, report
from evals.metrics import silent_action, states_action

GOOD = {"cases": 30, "errors": 0, "intent_accuracy": 0.97, "escalation_recall": 1.0, "groundedness": 1.0,
        "forbidden_tool_rate": 0.0, "action_correctness": 0.5, "placeholder_leaks": 0, "unbacked_action_claims": 0,
        "template_slots": 0, "wrong_refund_timelines": 0, "silent_actions": 0}


def results_file(tmp_path, **changes):
    metrics = {**GOOD, **changes}
    path = tmp_path / "results.json"
    path.write_text(json.dumps({"meta": {"provider": "openai", "model": "gpt", "subset": "smoke"},
                                "metrics": metrics, "cases": [{}]}), encoding="utf-8")
    return path


def run_gate(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["gate", *map(str, argv)])
    return gate.main()


def test_a_healthy_run_passes_the_default_gate(tmp_path, monkeypatch, capsys):
    assert run_gate(monkeypatch, results_file(tmp_path)) == 0
    assert "Gate passed" in capsys.readouterr().out


@pytest.mark.parametrize("change, named", [
    ({"escalation_recall": 0.857}, "escalation_recall is 0.857, below 0.95"),
    ({"forbidden_tool_rate": 0.033}, "forbidden_tool_rate is 0.033, above 0.0"),
    ({"intent_accuracy": 0.86}, "intent_accuracy"),
    ({"groundedness": 0.9}, "groundedness"),
    ({"silent_actions": 2}, "silent_actions is 2"),
    ({"errors": 1}, "errors is 1"),
])
def test_each_breach_fails_and_names_itself(tmp_path, monkeypatch, capsys, change, named):
    assert run_gate(monkeypatch, results_file(tmp_path, **change)) == 1
    assert named in capsys.readouterr().err


def test_thresholds_can_be_changed_and_extra_gates_turned_on(tmp_path, monkeypatch):
    path = results_file(tmp_path)
    assert run_gate(monkeypatch, path, "--min-action-correctness", "0.85") == 1
    assert run_gate(monkeypatch, path, "--min-intent-accuracy", "0.99") == 1
    assert run_gate(monkeypatch, results_file(tmp_path, intent_accuracy=0.9)) == 0


def test_a_metric_that_could_not_be_measured_is_shown_not_failed():
    rows = gate.check({**GOOD, "groundedness": None})
    assert next(r for r in rows if r["metric"] == "groundedness")["note"] == "not measured in this run"
    assert gate.breaches({**GOOD, "groundedness": None}) == []


def test_an_unreadable_or_empty_file_is_its_own_failure(tmp_path, monkeypatch):
    assert run_gate(monkeypatch, tmp_path / "missing.json") == 2
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"meta": {}, "metrics": {}, "cases": []}), encoding="utf-8")
    assert run_gate(monkeypatch, empty) == 2


def test_the_summary_goes_to_github_when_it_asks(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    run_gate(monkeypatch, results_file(tmp_path, escalation_recall=0.5))
    assert "| escalation_recall | 0.5 | >= 0.95 | hard | **FAIL** |" in summary.read_text(encoding="utf-8")


def test_a_silent_action_is_one_the_reply_never_mentions():
    refund = {"name": "issue_refund", "error": None, "result": {"refund_id": "RF-A9134-1", "amount_inr": 3049}}
    escalated = {"predicted": {"escalated": True}, "tool_calls": [refund],
                 "reply": "Hi, thank you for getting in touch about your refund request. I have passed it to our support team."}
    assert silent_action(escalated)
    assert not silent_action({**escalated, "reply": "Your refund of Rs 3,049 has been issued, reference RF-A9134-1."})
    assert not silent_action({**escalated, "predicted": {"escalated": False}})
    assert states_action("Your order A1 has been cancelled.", {"name": "cancel_order", "result": {}})
    assert not states_action("Thanks for your cancellation request.", {"name": "cancel_order", "result": {}})


def test_nightly_rows_and_the_comparison_table(tmp_path, monkeypatch):
    from evals.metrics import compute
    from evals.test_judge import record

    evals_md = tmp_path / "EVALS.md"
    evals_md.write_text("# Evals\n\n## Nightly runs\n\n| Run |\n| --- |\n\n## Model comparison\n\n"
                        f"{compare.START}\nnothing yet\n{compare.END}\n\n## After\n", encoding="utf-8")
    run = {"meta": {"provider": "openai", "model": "gpt-4o-mini", "subset": "full", "verify": True, "git_commit": "abc1234"},
           "metrics": compute([record()]), "cases": [record()]}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(run), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["report", str(path), "--nightly", "--file", str(evals_md)])
    assert report.main() == 0
    text = evals_md.read_text(encoding="utf-8")
    assert "openai gpt-4o-mini | 1 full |" in text and "| abc1234 | pass |" in text

    compare.write(evals_md, compare.table([path, path]))
    text = evals_md.read_text(encoding="utf-8")
    assert text.count("| openai gpt-4o-mini | 1 full | on |") == 2
    assert "nothing yet" not in text and text.endswith("## After\n")


def test_two_runs_of_the_same_models_can_be_told_apart_by_a_label(tmp_path):
    from evals.metrics import compute
    from evals.test_judge import record

    run = {"meta": {"provider": "openai", "model": "gpt-4o-mini", "subset": "full", "verify": True},
           "metrics": compute([record()]), "cases": [record()]}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(run), encoding="utf-8")

    text = compare.table([path, f"signals read early={path}"])
    assert "| openai gpt-4o-mini | 1 full |" in text
    assert "| openai gpt-4o-mini, signals read early | 1 full |" in text
    assert text.count("`results.json`") == 2
    assert compare.labelled(str(path)) == (None, path)
