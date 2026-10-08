from evals.history import history, in_ci, met, number, table_after, versions

TEXT = """# Evals

## Version history

Some words before the table.

| Version | Change | Model | Cases | Intent acc | Esc. recall | Cost per ticket | Latency p95 | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | Baseline | ollama qwen2.5:7b | 120 full | 0.88 | 0.93 | Rs 0.00 | 248.2 s | 2026-09-25 |
| v2 | + tools | ollama qwen2.5:7b | 120 full | 0.90 | n/a | Rs 0.15 | 7.2 s | 2026-09-26 |

Words after it.

## By category

| Version | Category | Cases | Decision acc | Deflection | Reply quality |
| --- | --- | --- | --- | --- | --- |
| v1 | all | 120 | 0.39 | 0.31 | n/a |
| v1 | adversarial | 18 | 0.61 | 0.11 | n/a |
| v2 | all | 120 | 0.53 | 0.42 | 4.20 |
"""


def test_a_table_is_read_by_its_heading_and_stops_where_it_ends():
    rows = table_after(TEXT, "## Version history")
    assert [r["Version"] for r in rows] == ["v1", "v2"] and rows[0]["Change"] == "Baseline"
    assert table_after(TEXT, "## Not there") == [] and table_after("## Version history\n\nno table", "## Version history") == []


def test_cells_become_numbers_whatever_unit_they_were_written_in():
    assert [number(t) for t in ("0.88", "Rs 0.15", "248.2 s", "n/a", "120 full", None)] == [0.88, 0.15, 248.2, None, None, None]


def test_a_version_carries_its_overall_row_from_the_category_table():
    v1, v2 = versions(TEXT)
    assert (v1["metrics"]["escalation_recall"], v1["metrics"]["deflection_rate"], v1["metrics"]["reply_quality"]) == (0.93, 0.31, None)
    assert (v2["metrics"]["escalation_recall"], v2["metrics"]["decision_accuracy"], v2["metrics"]["latency_s_p95"]) == (None, 0.53, 7.2)


def test_a_target_is_met_the_way_it_is_written():
    assert met("> 0.90", 0.93) and not met("> 0.90", 0.90)
    assert met("0.00", 0.0) and not met("0.00", 0.02)
    assert met("< Rs 0.60", 0.15) and met("< 8 s", 7.2) and not met("< 8 s", 449.0)
    assert met("report only", 0.23) is None and met("> 0.90", None) is None


def test_the_console_says_what_the_gate_does_today_and_not_what_the_target_hopes():
    assert in_ci("escalation_recall", "hard") == "hard gate, fails the build"
    assert in_ci("intent_accuracy", "yes") == "gate, fails the build"
    assert in_ci("action_correctness", "yes") == "reported, its gate is off for now"
    assert in_ci("deflection_rate", "report") == "reported, never gated on purpose"
    assert in_ci("latency_ms_p95", "no") == "reported"


def test_the_history_of_a_file_with_nothing_recorded_is_empty(tmp_path):
    empty = history(tmp_path / "missing.md")
    assert empty["versions"] == [] and empty["latest"] is None and len(empty["targets"]) == 10

    path = tmp_path / "EVALS.md"
    path.write_text(TEXT, encoding="utf-8")
    recorded = history(path)
    assert recorded["latest"] == "v2"
    shown = {row["metric"]: (row["shown"], row["met"]) for row in recorded["targets"]}
    assert shown["intent_accuracy"] == ("0.90", False) and shown["latency_ms_p95"] == ("7.2 s", True)
    assert shown["escalation_recall"] == ("n/a", None)
