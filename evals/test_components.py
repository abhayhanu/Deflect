import json
import sys
from types import SimpleNamespace

import pytest

from agent.state import Plan, Verification
from evals import components, gate, report
from evals.components import classifier, faults, first_fault, generator, guardrail, planner, retriever
from evals.metrics import compute
from evals.run import checker_rounds, plan_rounds
from evals.test_gate import GOOD, results_file, run_gate


def check(verdict="pass", failed=(), doubt=None, fell_back=False):
    return {"verdict": verdict, "failed_checks": list(failed), "sentence_doubt": doubt or {}, "checker_fell_back": fell_back}


def rec(case_id="g1", label="answer", intent="refund_request", said=None, need=("pol_a",), sections=("pol_a#One", "pol_b#Two"),
        plan="answer", ended="answered", rounds=None, guard=None, plans=None, tool=None):
    escalated = ended.startswith("escalated")
    return {
        "case_id": case_id, "error": None,
        "expected": {"intent": intent, "decision": label, "must_escalate": label == "escalate",
                     "required_policy_ids": list(need), "tool_name": tool, "tool_args": {"order_id": "A1"} if tool else None},
        "predicted": {"intent": said or intent, "decision": "escalate" if escalated else plan, "escalated": escalated,
                      "plan_decision": plan, "terminal_reason": ended, "retrieved_sections": list(sections)},
        "guardrail": guard, "approval": {"requested": False, "decision": None},
        "checker_rounds": rounds or [], "plan_rounds": plans or [],
    }


def test_the_retriever_is_scored_on_rank_and_per_policy():
    records = [rec("g1", sections=("pol_a#One", "pol_b#Two")),
               rec("g2", sections=("pol_b#One", "pol_b#Two", "pol_a#Three", "pol_c#Four")),
               rec("g3", need=("pol_esc",), sections=("pol_a#One", "pol_b#Two"), said="complaint"),
               rec("g4", need=(), sections=("pol_a#One",)), rec("g5", sections=())]
    r = retriever(records)

    # A ticket with no required policy, and one that never reached the search, are left out.
    assert (r["tickets"], r["complete"]) == (3, 2) and r["recall"] == pytest.approx(2 / 3, abs=1e-4)
    assert r["mrr"] == pytest.approx((1 + 1 / 3 + 0) / 3, abs=1e-4)
    assert r["precision"] == pytest.approx((1 / 2 + 1 / 4 + 0) / 3, abs=1e-4)
    assert r["per_policy"] == {"pol_a": {"needed": 2, "found": 2, "recall": 1.0}, "pol_esc": {"needed": 1, "found": 0, "recall": 0.0}}
    assert (r["recall_intent_right"], r["recall_intent_wrong"]) == (1.0, 0.0)
    assert r["misses"] == [{"case_id": "g3", "missing": ["pol_esc"], "intent_right": False}]


def test_the_search_is_not_scored_on_a_policy_the_plan_is_always_shown():
    pinned = ("pol_esc#Rules", "pol_esc#Told")
    found_by_pin = rec("g1", need=("pol_esc",), sections=("pol_a#One", "pol_b#Two", *pinned))
    found_by_search = rec("g2", sections=("pol_b#One", "pol_a#Two", *pinned))
    missed = rec("g3", need=("pol_c", "pol_esc"), sections=("pol_a#One", *pinned))
    for record in (found_by_pin, found_by_search, missed):
        record["predicted"]["pinned_sections"] = list(pinned)
    r = retriever([found_by_pin, found_by_search, missed])

    assert (r["tickets"], r["complete"], r["only_by_the_pin"]) == (3, 2, 1)
    assert r["per_policy"]["pol_esc"] == {"needed": 2, "found": 2, "recall": 1.0}
    # Rank and precision are over the two tickets where the search had something to find.
    assert r["ranked_tickets"] == 2 and r["mrr"] == pytest.approx((1 / 2 + 0) / 2, abs=1e-4)
    assert r["precision"] == pytest.approx((1 / 2 + 0) / 2, abs=1e-4)
    assert first_fault({**found_by_pin, "expected": {**found_by_pin["expected"], "decision": "answer"},
                        "predicted": {**found_by_pin["predicted"], "decision": "escalate", "plan_decision": "escalate"}}) == "planner"


def test_the_classifier_reports_every_intent_on_its_own():
    c = classifier([rec("g1"), rec("g2", intent="complaint", said="refund_request"), rec("g3", intent="complaint")])
    assert (c["right"], c["accuracy"]) == (2, pytest.approx(2 / 3, abs=1e-4))
    assert c["per_intent"]["complaint"] == {"labelled": 2, "right": 1, "recall": 0.5, "precision": 1.0}
    assert c["per_intent"]["refund_request"]["precision"] == 0.5
    assert c["confusions"] == [["complaint read as refund_request", 1]]


def test_the_planner_is_split_by_whether_its_inputs_were_right():
    records = [rec("g1"), rec("g2", plan="escalate", ended="escalated_by_plan"),
               rec("g3", said="complaint", plan="escalate", ended="escalated_by_plan"),
               rec("g4", label="escalate", plan="answer", sections=("pol_b#One",)), rec("g5", plan=None, ended="escalated_out_of_scope")]
    p = planner(records)

    assert (p["tickets"], p["right"]) == (4, 1)
    assert p["inputs_right"] == {"tickets": 2, "right": 1, "accuracy": 0.5}
    assert p["an_input_wrong"] == {"tickets": 2, "right": 0, "accuracy": 0.0}
    assert (p["sent_to_a_person_needlessly"], p["handled_what_needed_a_person"]) == (2, 1)
    assert p["by_label"]["answer"] == {"labelled": 3, "right": 1, "recall": pytest.approx(1 / 3, abs=1e-4)}


def test_the_first_plan_is_the_one_scored_when_the_run_kept_it():
    changed = rec("g1", label="act", tool="issue_refund", plan="escalate", ended="escalated_by_plan",
                  plans=[{"decision": "act", "tool_name": "issue_refund", "tool_args": {"order_id": "A1", "amount_inr": 5}},
                         {"decision": "escalate", "tool_name": None, "tool_args": {}}])
    wrong_tool = rec("g2", label="act", tool="issue_refund", plan="act", ended="acted",
                     plans=[{"decision": "act", "tool_name": "cancel_order", "tool_args": {"order_id": "A1"}}])
    p = planner([changed, wrong_tool])
    assert p["first_plan_recorded"] and p["right"] == 2
    assert (p["right_tool"], p["right_arguments"]) == (0.5, 0.5)


def test_the_generator_is_scored_on_first_drafts_only():
    records = [rec("g1", rounds=[check(doubt={"a": 0.1, "b": 0.2})]),
               rec("g2", rounds=[check("retry", ["unsupported_claims"], {"a": 0.9, "b": 0.1}), check()]),
               rec("g3", ended="escalated_verify_failed", sections=("pol_b#One",),
                   rounds=[check("retry", ["citation_not_retrieved"]), check("escalate", ["claims_unperformed_action"], fell_back=True)]),
               rec("g4", plan="escalate", ended="escalated_by_plan")]
    g = generator(records)

    assert (g["tickets"], g["first_draft_passed"], g["sent"]) == (3, 1, 2)
    assert g["first_draft_faults"] == {"unsupported_claims": 1} and g["stopped_by_the_plan"] == 1
    assert (g["sentences_checked"], g["sentences_supported"], g["sentence_support"]) == (4, 3, 0.75)
    assert (g["pass_rate_policies_complete"], g["pass_rate_a_policy_missing"], g["tickets_a_policy_missing"]) == (0.5, 0.0, 1)
    assert g["drafts_per_ticket"] == pytest.approx(5 / 3, abs=0.01) and g["stand_ins"] == 1


def test_the_guardrail_separates_refusals_the_label_agrees_with():
    deny = {"outcome": "deny", "check": "policy_rules", "detail": "x"}
    g = guardrail([rec("g1", plan="act", guard={"outcome": "allow", "check": None}, label="act", tool="issue_refund", ended="acted"),
                   rec("g2", plan="act", guard=deny, ended="escalated_guardrail_policy_rules"),
                   rec("g3", plan="act", guard=deny, label="act", tool="issue_refund", ended="escalated_guardrail_policy_rules"), rec("g4")])
    assert (g["tickets"], g["allowed"], g["denied"]) == (3, 1, 2)
    assert (g["denied_no_action_wanted"], g["denied_action_wanted"]) == (["g2"], ["g3"])


@pytest.mark.parametrize("record, fault", [
    (rec(), None),
    (rec(label="escalate", plan="answer"), None),
    (rec(said="complaint", plan="escalate", ended="escalated_by_plan", sections=("pol_b#One",)), "classifier"),
    (rec(plan=None, ended="escalated_low_confidence"), "classifier"),
    (rec(plan=None, ended="escalated_message_signal", sections=()), "guardrail"),
    (rec(said="complaint", plan=None, ended="escalated_message_signal", sections=()), "guardrail"),
    (rec(plan="escalate", ended="escalated_by_plan", sections=("pol_b#One",)), "retriever"),
    (rec(plan="escalate", ended="escalated_by_plan"), "planner"),
    (rec(label="act", tool="issue_refund", plan="act", ended="escalated_guardrail_arguments", guard={"outcome": "deny", "check": "arguments"}), "guardrail"),
    (rec(ended="escalated_verify_failed"), "generator or checker"),
    (rec(label="act", tool="issue_refund", plan="act", ended="escalated_tool_error"), "other"),
])
def test_the_first_fault_is_the_earliest_step_that_was_wrong(record, fault):
    assert first_fault(record) == fault


def test_faults_add_up_to_the_tickets_that_should_have_been_closed():
    records = [rec("g1"), rec("g2", plan="escalate", ended="escalated_by_plan"), rec("g3", label="escalate", plan="escalate", ended="escalated_by_plan")]
    f = faults(records)
    assert (f["should_close"], f["closed_right"], f["lost"]) == (2, 1, 1)
    assert f["first_fault"]["planner"] == 1 and f["tickets"]["planner"] == ["g2"]


def test_the_table_replaces_its_block_and_names_a_mixed_run(tmp_path):
    records = [rec("g1", rounds=[check(fell_back=True)]), rec("g2", plan="escalate", ended="escalated_by_plan")]
    meta = {"provider": "ollama", "model": "qwen2.5:7b", "subset": "full", "verify": True, "classifier": "jev:jev-latest"}
    block = components.markdown(components.compute(records), meta, "results_v9.json")
    assert "| planner | Last plan matches the label | 0.50 | 1 of 2 |" in block
    assert "classified by jev jev-latest" in block and "describe a mix" in block

    page = tmp_path / "EVALS.md"
    page.write_text(f"# Evals\n\n{components.START}\nold\n{components.END}\n\n## After\n", encoding="utf-8")
    components.write(page, block)
    text = page.read_text(encoding="utf-8")
    assert "old" not in text and block in text and text.endswith("## After\n")


def test_a_run_with_stand_ins_fails_the_gate_and_an_older_file_does_not(tmp_path, monkeypatch, capsys):
    assert run_gate(monkeypatch, results_file(tmp_path, classifier_fallbacks=18, checker_fallbacks=0)) == 1
    assert "classifier_fallbacks is 18" in capsys.readouterr().err
    assert run_gate(monkeypatch, results_file(tmp_path, classifier_fallbacks=0, checker_fallbacks=3)) == 1
    assert run_gate(monkeypatch, results_file(tmp_path, classifier_fallbacks=0, checker_fallbacks=0)) == 0
    # A file from before the classifier setting has no such count, and that is not a breach.
    assert gate.breaches(GOOD) == []


def test_a_run_with_stand_ins_is_not_recorded_unless_it_says_so_in_its_row(tmp_path, monkeypatch, capsys):
    from evals.test_metrics import record

    cases = [record("g1", "complaint", "complaint", True, True)]
    cases[0]["predicted"]["classifier_fell_back"] = True
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"meta": {"provider": "ollama", "model": "qwen2.5:7b", "subset": "full"},
                                   "metrics": compute(cases), "cases": cases}), encoding="utf-8")
    page = tmp_path / "EVALS.md"
    page.write_text("".join(f"{h}\n\n| a |\n| --- |\n\n" for h in (report.TABLE_START, report.ADVERSARIAL_START, report.CONTROLS_START,
                                                                 report.CATEGORY_START)), encoding="utf-8")
    argv = ["report", str(results), "--version", "v9", "--change", "+ something", "--file", str(page)]

    monkeypatch.setattr(sys, "argv", argv)
    assert report.main() == 1
    assert "1 tickets classified and 0 replies checked by a stand in" in capsys.readouterr().err
    assert "| v9 |" not in page.read_text(encoding="utf-8")

    monkeypatch.setattr(sys, "argv", [*argv, "--allow-fallbacks"])
    assert report.main() == 0
    assert "| v9 | + something. 1 tickets classified and 0 replies checked by a stand in |" in page.read_text(encoding="utf-8")


def test_every_plan_and_every_verdict_is_read_from_the_history():
    def step(after, **values):
        return SimpleNamespace(next=(after,), values=values)

    first = Plan(decision="act", tool_name="issue_refund", tool_args={"order_id": "A1", "idempotency_key": "k"}, cites=["pol_a"], rationale="r")
    second = Plan(decision="escalate", tool_name=None, tool_args=None, cites=[], rationale="r", escalation_reason="unsure")
    verdict = Verification(grounded=False, action_matches_policy=True, unsupported_claims=["x"], verdict="retry", failed_checks=["unsupported_claims"])
    history = [step("plan"), step("draft", plan=first), step("verify", plan=first, draft="d1"),
               step("plan", plan=first, draft="d1", verification=verdict), step("escalate", plan=second, draft="d1", verification=verdict)]

    plans = plan_rounds(history)
    assert [p["decision"] for p in plans] == ["act", "escalate"]
    assert plans[0]["tool_args"] == {"order_id": "A1"} and plans[1]["escalation_reason"] == "unsure"
    assert [(c["verdict"], c["draft"]) for c in checker_rounds(history)] == [("retry", "d1")]


def test_a_second_plan_after_a_rejected_reply_is_kept_from_a_real_run(scripted, fake_tools):
    from agent.approvals import MemoryApprovals
    from agent.context import RunContext
    from agent.graph import build_graph, memory_checkpointer
    from agent.guardrails.audit import MemoryAuditLog
    from agent.test_graph import CASE, MESSAGE, NOW, ORDER, POLICY_HIT, classification, plan

    tools = fake_tools({"search_policy": [POLICY_HIT], "get_order": ORDER, "get_customer_history": None, "escalate_to_human": CASE})
    model = scripted({"Classification": [classification()], "Plan": [plan(), plan("escalate", reason="unsure")],
                      "ClaimCheck": [{"unsupported_claims": ["It arrives tomorrow."]}]}, texts=["It is on its way. It arrives tomorrow."])
    graph = build_graph(memory_checkpointer())
    config = {"configurable": {"thread_id": "t1"}}
    ctx = RunContext(model=model, now=NOW, tools=tools, audit=MemoryAuditLog(), approvals=MemoryApprovals())
    graph.invoke({"ticket_id": "T1", "raw_message": MESSAGE, "customer_id": "C_1140", "channel": "chat"}, config, context=ctx)

    history = list(graph.get_state_history(config))[::-1]
    assert [p["decision"] for p in plan_rounds(history)] == ["answer", "escalate"]
    assert [c["verdict"] for c in checker_rounds(history)] == ["retry"]
