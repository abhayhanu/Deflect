import json

import pytest

from evals.validate import GOLDEN_FILE, POLICY_DIR, load_seed_offline, validate


@pytest.fixture(scope="module")
def seed():
    return load_seed_offline()


@pytest.fixture
def cases():
    return [json.loads(line) for line in GOLDEN_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]


def run(tmp_path, seed, cases):
    path = tmp_path / "tickets.jsonl"
    path.write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    report, _, _ = validate(path, POLICY_DIR, seed)
    return report.errors


def find(cases, case_id):
    return next(c for c in cases if c["case_id"] == case_id)


def test_committed_dataset_passes(tmp_path, seed, cases):
    assert run(tmp_path, seed, cases) == []


def test_duplicate_case_id_is_caught(tmp_path, seed, cases):
    cases[1]["case_id"] = cases[0]["case_id"]
    assert any("used 2 times" in e for e in run(tmp_path, seed, cases))


def test_unknown_policy_is_caught(tmp_path, seed, cases):
    find(cases, "gold_011")["expected"]["required_policy_ids"] = ["pol_made_up"]
    assert any("pol_made_up" in e for e in run(tmp_path, seed, cases))


def test_refund_larger_than_order_is_caught(tmp_path, seed, cases):
    find(cases, "gold_011")["expected"]["tool_args"]["amount_inr"] = 9_999
    assert any("exceeds what is left" in e for e in run(tmp_path, seed, cases))


def test_tool_outside_allowlist_is_caught(tmp_path, seed, cases):
    case = find(cases, "gold_053")
    case["expected"].update(decision="act", must_escalate=False, tool_name="issue_refund",
                            tool_args={"order_id": case["order_id"]})
    assert any("not allowed for intent complaint" in e for e in run(tmp_path, seed, cases))


def test_escalation_mismatch_is_caught(tmp_path, seed, cases):
    find(cases, "gold_053")["expected"]["decision"] = "answer"
    assert any("must_escalate is true" in e for e in run(tmp_path, seed, cases))


def test_order_of_another_customer_is_caught(tmp_path, seed, cases):
    find(cases, "gold_001")["customer_id"] = "C_1199"
    assert any("belongs to C_1140" in e for e in run(tmp_path, seed, cases))
