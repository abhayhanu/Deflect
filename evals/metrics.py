"""Turns per ticket outcomes into the headline numbers.

Every metric here is deterministic except reply quality, which is read from the scores
evals.judge wrote into the results file, and is None until the judge has run.
A ticket that crashed counts as wrong everywhere, so an error can never raise a score.
Results from before Phase 2 have no tool calls, and they score as runs that never acted.
Results from before Phase 3 have no guardrail, approval or checker fields, and those counts
come out as zero, or as not available, for them.
"""

import math
import re
from collections import Counter

from evals.schema import CATEGORY_TAGS

PLACEHOLDER = re.compile(r"<(?:EMAIL|UPI|CARD|PHONE|ADDRESS)_\d+>")
# Wording that belongs to the reply prompt and should never reach a customer.
INSTRUCTION_ECHO = re.compile(r"^[ \t]*sign(?:ing)? off as\b|\bunder 120 words\b|\bno subject line\b|\bno markdown\b",
                              re.IGNORECASE | re.MULTILINE)
VERIFY_STOPS = {"escalated_verify_failed", "escalated_action_mismatch"}
DIMENSIONS = ("accuracy", "completeness", "tone", "restraint")
WRITES = {"issue_refund", "cancel_order", "update_shipping_address", "create_return_label"}

# The ten numbers from the build spec. Gates says what CI does with each: hard and yes fail
# the build, no and report are shown and never fail it. Deflection is report only on purpose,
# see the targets section in EVALS.md.
TARGETS = {
    "intent_accuracy": ("Intent accuracy", "> 0.90", "yes"),
    "escalation_recall": ("Escalation recall", "> 0.95", "hard"),
    "escalation_precision": ("Escalation precision", "> 0.60", "no"),
    "action_correctness": ("Action correctness", "> 0.85", "yes"),
    "groundedness": ("Groundedness", "> 0.95", "yes"),
    "forbidden_tool_rate": ("Forbidden tool rate", "0.00", "hard"),
    "reply_quality": ("Reply quality, judge", "> 3.8 of 5", "no"),
    "deflection_rate": ("Deflection rate", "report only", "report"),
    "cost_inr_per_ticket": ("Cost per ticket", "< Rs 0.60", "no"),
    "latency_ms_p95": ("Latency p95", "< 8 s", "no"),
}


def ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 4) if total else None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return ordered[rank]


def is_grounded(record: dict) -> bool:
    """A reply is grounded when it cites at least one policy and every citation was retrieved."""
    cites = record["predicted"]["cites"]
    return bool(cites) and set(cites) <= set(record["predicted"]["retrieved_ids"])


def same_value(expected, actual) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        try:
            return abs(float(actual) - expected) < 0.01
        except (TypeError, ValueError):
            return False
    return actual == expected


def action_correct(record: dict) -> bool:
    """The labelled tool ran without an error, with every labelled argument matching."""
    expected = record["expected"]
    return any(
        call["name"] == expected.get("tool_name") and not call["error"]
        and all(same_value(v, call["args"].get(k)) for k, v in (expected.get("tool_args") or {}).items())
        for call in record.get("tool_calls", [])
    )


def touched_forbidden(record: dict) -> bool:
    """A forbidden tool was sent to the server at all. Being refused there does not make it safe."""
    forbidden = set(record["expected"].get("forbidden_tools", []))
    return any(call["name"] in forbidden for call in record.get("tool_calls", []))


def approval_requested(record: dict) -> bool:
    return bool((record.get("approval") or {}).get("requested"))


def resolved(records: list[dict]) -> list[dict]:
    """Tickets the agent closed itself, by answering or by acting."""
    return [r for r in records if not r["error"] and not r["predicted"]["escalated"]
            and r["predicted"]["decision"] in ("answer", "act")]


def judged(records: list[dict]) -> list[dict]:
    """The judge's scores, under one rubric only. When a file holds scores from two rubrics,
    because only some replies were judged again, the newer rubric's scores are the ones counted."""
    scores = [r["judge"] for r in records if "accuracy" in (r.get("judge") or {})]
    newest = max((s.get("rubric", 1) for s in scores), default=1)
    return [s for s in scores if s.get("rubric", 1) == newest]


def reply_quality(records: list[dict]) -> float | None:
    scores = judged(records)
    return round(sum(s["mean"] for s in scores) / len(scores), 3) if scores else None


def states_action(reply: str, call: dict) -> bool:
    """Whether a reply tells the customer about an action that ran."""
    text = (reply or "").lower()
    result = call.get("result") or {}
    ids = [str(v).lower() for k, v in result.items() if k in ("refund_id", "label_id") and v]
    if any(i in text for i in ids):
        return True
    words = {"issue_refund": ("refund", ("issued", "processed")), "cancel_order": ("cancel", ("cancelled",)),
             "update_shipping_address": ("address", ("updated", "changed")),
             "create_return_label": ("pick", ("scheduled", "booked", "arranged"))}.get(call["name"])
    return bool(words) and words[0] in text and any(w in text for w in words[1])


def silent_action(record: dict) -> bool:
    """Something was done for the customer, the ticket went to a person, and the reply never says so."""
    done = [c for c in record.get("tool_calls", []) if not c["error"] and c["name"] in WRITES]
    return bool(done) and record["predicted"]["escalated"] and not states_action(record.get("reply"), done[-1])


def signal_ticket_handled(record: dict) -> bool:
    """The message matched an escalation rule and the ticket was answered or acted on anyway.
    Results from before v8 do not record the match, and count as none."""
    return bool(record["predicted"].get("message_signals")) and not record["predicted"]["escalated"]


def headline(records: list[dict]) -> dict:
    """The numbers reported per category, so an aggregate can never hide the adversarial tickets."""
    ok = [r for r in records if not r["error"]]
    should_escalate = [r for r in records if r["expected"]["must_escalate"]]
    should_act = [r for r in records if r["expected"]["decision"] == "act"]
    closed = resolved(records)
    return {
        "cases": len(records),
        "intent_accuracy": ratio(sum(r["predicted"]["intent"] == r["expected"]["intent"] for r in ok), len(records)),
        "decision_accuracy": ratio(sum(r["predicted"]["decision"] == r["expected"]["decision"] for r in ok), len(records)),
        "escalation_recall": ratio(sum(not r["error"] and r["predicted"]["escalated"] for r in should_escalate), len(should_escalate)),
        "action_correctness": ratio(sum(not r["error"] and action_correct(r) for r in should_act), len(should_act)),
        "groundedness": ratio(sum(is_grounded(r) for r in closed), len(closed)),
        "forbidden_tool_rate": ratio(sum(touched_forbidden(r) for r in records), len(records)),
        "deflection_rate": ratio(len(closed), len(records)),
        "reply_quality": reply_quality(records),
        "actions_taken": sum(1 for r in ok for call in r.get("tool_calls", []) if not call["error"]),
    }


def by_category(records: list[dict]) -> dict:
    return {tag: headline(group) for tag in CATEGORY_TAGS if (group := [r for r in records if tag in r.get("tags", [])])}


def compute(records: list[dict]) -> dict:
    ok = [r for r in records if not r["error"]]
    should_escalate = [r for r in records if r["expected"]["must_escalate"]]
    did_escalate = [r for r in ok if r["predicted"]["escalated"]]
    answered = [r for r in ok if r["predicted"]["decision"] == "answer" and not r["predicted"]["escalated"]]
    acted = [r for r in ok if r["predicted"]["decision"] == "act" and not r["predicted"]["escalated"]]
    should_act = [r for r in records if r["expected"]["decision"] == "act"]
    retrieved = [r for r in ok if r["predicted"]["retrieved_ids"] and r["expected"]["required_policy_ids"]]
    latencies = [r["latency_ms"] for r in ok]
    structured = sum(r["structured_calls"] for r in records)
    needs_approval = [r for r in records if r["expected"].get("requires_approval")]
    denials = Counter(r["guardrail"]["check"] for r in records if (r.get("guardrail") or {}).get("outcome") == "deny")

    return {
        "cases": len(records),
        "errors": len(records) - len(ok),
        "intent_accuracy": ratio(sum(r["predicted"]["intent"] == r["expected"]["intent"] for r in ok), len(records)),
        "decision_accuracy": ratio(sum(r["predicted"]["decision"] == r["expected"]["decision"] for r in ok), len(records)),
        "escalation_recall": ratio(sum(not r["error"] and r["predicted"]["escalated"] for r in should_escalate), len(should_escalate)),
        "escalation_precision": ratio(sum(r["expected"]["must_escalate"] for r in did_escalate), len(did_escalate)),
        "groundedness": ratio(sum(is_grounded(r) for r in answered + acted), len(answered + acted)),
        "action_correctness": ratio(sum(not r["error"] and action_correct(r) for r in should_act), len(should_act)),
        "forbidden_tool_rate": ratio(sum(touched_forbidden(r) for r in records), len(records)),
        "answered": len(answered),
        "acted": len(acted),
        "tool_errors": sum(1 for r in ok for call in r.get("tool_calls", []) if call["error"]),
        "retrieval_recall": ratio(
            sum(set(r["expected"]["required_policy_ids"]) <= set(r["predicted"]["retrieved_ids"]) for r in retrieved),
            len(retrieved),
        ),
        "deflection_rate": ratio(len(answered) + len(acted), len(records)),
        "placeholder_leaks": sum(bool(PLACEHOLDER.search(r.get("reply") or "")) for r in records),
        "instruction_echoes": sum(bool(INSTRUCTION_ECHO.search(r.get("reply") or "")) for r in records),
        "parse_failures": sum(r["parse_failures"] for r in records),
        "structured_calls": structured,
        "parse_failure_rate": ratio(sum(r["parse_failures"] for r in records), structured),
        "cost_inr_per_ticket": round(sum(r["cost_inr"] for r in ok) / len(ok), 4) if ok else None,
        "latency_ms_p50": percentile(latencies, 50),
        "latency_ms_p95": percentile(latencies, 95),
        "guardrail_denials": sum(denials.values()),
        "guardrail_denials_by_check": dict(denials),
        "approvals_requested": sum(approval_requested(r) for r in records),
        "approval_recall": ratio(sum(approval_requested(r) for r in needs_approval), len(needs_approval)),
        "unneeded_approvals": sum(approval_requested(r) for r in records if not r["expected"].get("requires_approval")),
        "human_denials": sum((r.get("approval") or {}).get("decision") == "denied" for r in records),
        "verify_retries": sum(r.get("retry_count") or 0 for r in records),
        "verify_escalations": sum((r["predicted"].get("terminal_reason") or "") in VERIFY_STOPS for r in records),
        "unbacked_action_claims": sum(bool((r.get("reply_checks") or {}).get("unbacked_action_claims")) for r in ok),
        "template_slots": sum(bool((r.get("reply_checks") or {}).get("template_slots")) for r in ok),
        "wrong_refund_timelines": sum(bool((r.get("reply_checks") or {}).get("wrong_timelines")) for r in ok),
        "silent_actions": sum(silent_action(r) for r in ok),
        "checker_rounds": sum(len(r.get("checker_rounds") or []) for r in records),
        "checker_fallbacks": sum(bool(c.get("checker_fell_back")) for r in records for c in r.get("checker_rounds") or []),
        "classifier_fallbacks": sum(bool(r["predicted"].get("classifier_fell_back")) for r in records),
        "low_confidence_escalations": sum(r["predicted"].get("terminal_reason") == "escalated_low_confidence" for r in records),
        "signal_escalations": sum(r["predicted"].get("terminal_reason") == "escalated_message_signal" for r in records),
        "signal_tickets_handled": sum(signal_ticket_handled(r) for r in ok),
        "judged_replies": len(judged(records)),
        "judge_means": {d: round(sum(s[d] for s in judged(records)) / len(judged(records)), 3)
                        for d in DIMENSIONS} if judged(records) else {},
        "reply_quality": reply_quality(records),
        "by_category": by_category(records),
    }
