import json
import sys

from evals import stability
from evals.run import load_cases


def case(case_id, escalated, reason, must=True, error=None):
    return {"case_id": case_id, "expected": {"must_escalate": must},
            "predicted": {"escalated": escalated, "decision": "escalate" if escalated else "answer",
                          "terminal_reason": reason},
            "error": error}


def run(*cases, commit="abc"):
    return {"meta": {"provider": "gemini", "model": "m", "classifier": "jev", "checker": "jev", "git_commit": commit},
            "cases": list(cases)}


RUNS = [
    run(case("g1", True, "escalated_message_signal"), case("g2", True, "escalated_by_plan"),
        case("g3", False, "answered"), case("g4", False, "answered", must=False)),
    run(case("g1", True, "escalated_message_signal"), case("g2", False, "answered"),
        case("g3", False, "answered"), case("g4", True, "escalated_by_plan", must=False)),
    run(case("g1", True, "escalated_message_signal"), case("g2", True, "escalated_by_plan"),
        case("g3", False, "answered"), case("g4", False, "answered", must=False)),
]


def test_each_ticket_is_counted_across_every_run():
    s = stability.compare(RUNS)
    assert s["must_escalate"] == 3
    assert s["recall_per_run"] == [0.6667, 0.3333, 0.6667]
    assert s["lowest_recall"] == 0.3333
    assert s["always"] == ["g1"]
    assert [(t["case_id"], t["escalated"]) for t in s["sometimes"]] == [("g2", 2)]
    assert [t["case_id"] for t in s["never"]] == ["g3"]


def test_a_rule_in_code_is_told_apart_from_a_models_judgement():
    s = stability.compare(RUNS)
    assert s["held_by_code"] == ["g1"]
    assert stability.decided_by_code("escalated_guardrail_refund_ceiling")
    assert not stability.decided_by_code("escalated_low_confidence")
    assert not stability.decided_by_code(None)


def test_a_changed_decision_is_listed_even_when_the_ticket_need_not_escalate():
    assert stability.compare(RUNS)["decisions_that_changed"] == ["g2", "g4"]


def test_a_run_that_crashed_on_a_ticket_did_not_escalate_it():
    crashed = run(case("g1", True, "escalated_message_signal", error="boom"))
    s = stability.compare([RUNS[0], crashed])
    assert s["tickets"] == 1
    assert s["recall_per_run"] == [1.0, 0.0]


def test_runs_on_different_code_are_called_out(tmp_path, monkeypatch, capsys):
    paths = []
    for n, commit in enumerate(("abc", "def")):
        path = tmp_path / f"pass_{n}.json"
        path.write_text(json.dumps(run(*RUNS[n]["cases"], commit=commit)), encoding="utf-8")
        paths.append(str(path))
    out = tmp_path / "steady.json"
    monkeypatch.setattr(sys, "argv", ["stability", *paths, "--out", str(out)])

    assert stability.main() == 0
    printed = capsys.readouterr()
    assert "different commits" in printed.err
    assert "lowest 0.33" in printed.out
    assert json.loads(out.read_text(encoding="utf-8"))["files"] == ["pass_0.json", "pass_1.json"]


def test_the_escalation_subset_is_every_ticket_that_must_reach_a_person():
    chosen = load_cases("escalation")
    assert len(chosen) == 30
    assert all(c.expected.must_escalate for c in chosen)
    assert {c.case_id for c in load_cases("full") if c.expected.must_escalate} == {c.case_id for c in chosen}
