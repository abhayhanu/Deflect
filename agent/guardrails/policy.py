"""Hard limits the graph enforces in code, whatever the model says.

Everything here is data: which tools each intent may reach, the money thresholds, and the
numeric rules from the policy documents. The guardrail node reads these tables. Changing a
limit is a change to this file and nothing else.
"""

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from agent.state import Classification, Intent, TicketState

# Thresholds

CONFIDENCE_FLOOR = 0.6
MAX_LOOPS = 4
MAX_TOOL_CALLS = 4
MAX_VERIFY_RETRIES = 2
# A sentence is sent back when the checker thinks it is at least this likely to be unsupported.
CHECKER_UNSUPPORTED_AT = 0.5
# Shorter sentences are greetings and sign offs, so the checker is not asked about them.
CHECKER_MIN_WORDS = 3
AUTO_APPROVE_REFUND_INR = 3_000
HARD_REFUND_CEILING_INR = 25_000
COST_CAP_INR = 2.00
TOOL_TIMEOUT_S = 10.0
DUPLICATE_REFUND_WINDOW = timedelta(hours=24)

# The classify node writes this as the reasoning when the model never produced valid output.
PARSE_FAILURE_NOTE = "Structured output failed to validate after every retry."

# Which tools an intent may ever reach. A tool missing from a row cannot be called for that
# intent, whatever the plan says. Complaints reach no tool that changes anything: a complaint
# ends in an answer or a human, because that is where an agent is most easily talked into a
# goodwill refund, and a closed path needs no defending.
TOOL_ALLOWLIST: dict[Intent, set[str]] = {
    "order_status": {"get_order", "search_policy", "check_shipment"},
    "refund_request": {"get_order", "search_policy", "check_shipment", "issue_refund"},
    "return_request": {"get_order", "search_policy", "check_shipment", "create_return_label"},
    "address_change": {"get_order", "search_policy", "check_shipment", "update_shipping_address"},
    "cancellation": {"get_order", "search_policy", "cancel_order", "issue_refund"},
    "complaint": {"get_order", "search_policy", "check_shipment"},
    "product_question": {"search_policy"},
    "out_of_scope": set(),
}

# Tools that move money, so a recent refund on the same order blocks them.
MONEY_TOOLS = {"issue_refund", "cancel_order"}

# The only policy that can back a refund with each reason. Goodwill and late delivery are
# missing on purpose: no policy grants them, so a refund with either reason is always refused.
REFUND_POLICY = {
    "lost_in_transit": "pol_lost_transit",
    "damaged": "pol_damaged_goods",
    "not_as_described": "pol_not_as_described",
}

# Categories that can never go back for a change of mind, from pol_return_window. Personal care
# is left out because it depends on whether the seal was opened, which only a person can judge.
NO_CHANGE_OF_MIND_RETURN = {
    "innerwear", "earphones_in_ear", "gift_card", "grocery", "customised", "phone", "laptop", "tablet",
}

# Argument values a tool uses when the plan leaves them out.
TOOL_DEFAULTS = {"create_return_label": {"reason": "change_of_mind"}}


@dataclass(frozen=True)
class Arrival:
    low: int
    high: int
    unit: str
    says: str

    @property
    def phrase(self) -> str:
        return f"within {self.says}" if self.unit == "hours" else f"in {self.says}"


# How long a refund takes to reach the customer once issued, by payment method, from
# pol_refund_timelines. v4 replies told customers 3 to 5 days for UPI and for cards, both wrong.
REFUND_ARRIVAL = {
    "wallet": Arrival(24, 24, "hours", "24 hours"),
    "upi": Arrival(1, 3, "days", "1 to 3 business days"),
    "netbanking": Arrival(3, 5, "days", "3 to 5 business days"),
    "card": Arrival(5, 7, "days", "5 to 7 business days"),
    "cod": Arrival(5, 7, "days", "5 to 7 business days by NEFT, after the bank details are shared"),
}


def hours(value) -> float | None:
    return None if value is None else float(value)


def delivered_within(order: dict, limit_h: float) -> bool:
    delivered = hours(order.get("hours_since_delivered"))
    return delivered is not None and delivered <= limit_h


def counts_as_lost(order: dict) -> bool:
    delivered = hours(order.get("hours_since_delivered"))
    if delivered is not None:
        return delivered >= 48
    late = hours(order.get("hours_past_promised_date"))
    return order.get("status") == "shipped" and late is not None and late > 7 * 24


def recent_lost_refund(history: dict | None) -> bool:
    refunds = (history or {}).get("refunds", [])
    return any(r.get("reason_code") == "lost_in_transit" and (r.get("days_ago") or 0) <= 90 for r in refunds)


def same_text(a, b) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


@dataclass(frozen=True)
class Rule:
    """One numeric rule from a policy document. A rule applies to a tool, and optionally only
    when an argument has a given value. check receives the arguments, the order facts and the
    customer history, and returns True when the action is allowed."""

    tool: str
    policy: str
    says: str
    check: Callable[[dict, dict, dict | None], bool]
    when: dict = field(default_factory=dict)

    def applies(self, tool: str, args: dict) -> bool:
        if tool != self.tool:
            return False
        full = {**TOOL_DEFAULTS.get(tool, {}), **args}
        return all(full.get(k) == v for k, v in self.when.items())


POLICY_RULES: list[Rule] = [
    Rule("cancel_order", "pol_cancellation", "only an order that has not shipped can be cancelled",
         lambda a, o, h: o["status"] == "placed"),

    Rule("update_shipping_address", "pol_address_change", "the address can only change before the order ships",
         lambda a, o, h: o["status"] == "placed"),
    Rule("update_shipping_address", "pol_address_change", "the new address must be in the same city",
         lambda a, o, h: same_text(a.get("city"), o.get("shipping_city"))),
    Rule("update_shipping_address", "pol_address_change", "orders above Rs 25,000 need an identity check first",
         lambda a, o, h: o["total_inr"] <= 25_000),

    Rule("create_return_label", "pol_return_window", "only a delivered order can be returned",
         lambda a, o, h: o["status"] == "delivered"),
    Rule("create_return_label", "pol_return_window", "a return must be requested within 7 days of delivery",
         lambda a, o, h: delivered_within(o, 168)),
    Rule("create_return_label", "pol_return_window", "phones, laptops, tablets, in ear audio, innerwear, gift cards, "
         "groceries and customised items cannot be returned for a change of mind",
         lambda a, o, h: not any(i.get("category") in NO_CHANGE_OF_MIND_RETURN for i in o.get("items", [])),
         when={"reason": "change_of_mind"}),

    Rule("issue_refund", "pol_escalation", "a refund must cite the policy that grants its reason, "
         "and no policy grants a goodwill or late delivery refund",
         lambda a, o, h: REFUND_POLICY.get(a.get("reason_code")) == a.get("policy_doc_id")),
    Rule("issue_refund", "pol_refund_timelines", "the total refunded can never exceed what was paid",
         lambda a, o, h: float(a["amount_inr"]) <= float(o["refundable_inr"]) + 0.01),

    Rule("issue_refund", "pol_lost_transit", "orders above Rs 5,000 need a carrier investigation first",
         lambda a, o, h: o["total_inr"] <= 5_000, when={"reason_code": "lost_in_transit"}),
    Rule("issue_refund", "pol_lost_transit", "a parcel is lost only 48 hours after the delivery scan, "
         "or more than 7 days after the promised date",
         lambda a, o, h: counts_as_lost(o), when={"reason_code": "lost_in_transit"}),
    Rule("issue_refund", "pol_lost_transit", "a claim after 15 days goes to the support team",
         lambda a, o, h: o.get("hours_since_delivered") is None or delivered_within(o, 15 * 24),
         when={"reason_code": "lost_in_transit"}),
    Rule("issue_refund", "pol_lost_transit", "one automatic lost in transit refund per customer in 90 days",
         lambda a, o, h: not recent_lost_refund(h), when={"reason_code": "lost_in_transit"}),

    Rule("issue_refund", "pol_damaged_goods", "damage must be reported within 72 hours of delivery",
         lambda a, o, h: delivered_within(o, 72), when={"reason_code": "damaged"}),
    Rule("issue_refund", "pol_damaged_goods", "items above Rs 10,000 need an inspection pickup first",
         lambda a, o, h: float(a["amount_inr"]) <= 10_000, when={"reason_code": "damaged"}),

    Rule("issue_refund", "pol_not_as_described", "a claim must be raised within 7 days of delivery",
         lambda a, o, h: delivered_within(o, 168), when={"reason_code": "not_as_described"}),
    Rule("issue_refund", "pol_not_as_described", "items above Rs 7,500 must be picked up and verified first",
         lambda a, o, h: float(a["amount_inr"]) <= 7_500, when={"reason_code": "not_as_described"}),
]


@dataclass(frozen=True)
class Signal:
    """One situation from pol_escalation that can be recognised from the customer's words alone."""

    rule: int
    says: str
    pattern: re.Pattern

    def cited(self) -> str:
        return f"the message {self.says} (pol_escalation rule {self.rule})"


def signal(rule: int, says: str, *patterns: str) -> Signal:
    return Signal(rule, says, re.compile("|".join(patterns), re.IGNORECASE))


TOOL_NAMES = "issue_refund|cancel_order|update_shipping_address|create_return_label|escalate_to_human"

# pol_escalation says these situations always go to a person and that nothing on the order
# changes, even where another policy would allow it. The plan is told this in its prompt, and a
# prompt is not a guardrail: a hosted model cancelled an order for someone claiming to be staff.
# The wording below comes from the policy's own lists, so it is a floor. It catches the phrases
# the policy names and will miss a careful paraphrase.
ESCALATION_SIGNALS: list[Signal] = [
    signal(1, "reports a safety incident",
           r"\b(started|is|was|began) smoking\b", r"\bsmoke (came|coming|started)", r"\bcaught fire\b", r"\bon fire\b",
           r"\bsparks?\b", r"\bburning smell\b", r"\belectric(al)? shock\b", r"\bburn(t|ed) (my|me|his|her|our)\b",
           r"\binjur(y|ed|ies)\b", r"\b(swollen|leaking|bulging) battery\b",
           r"\bbattery (is |has |was )?(swollen|swelled|leaking|bulging|exploded)\b", r"\ballergic\b"),
    signal(2, "mentions legal action",
           r"\b(lawyer|advocate|attorney)\b", r"\blegal (notice|action)\b", r"\bconsumer (court|forum|commission|helpline)\b",
           r"\bpolice (complaint|report)\b", r"\bfile (a|an) (case|fir)\b", r"\bsue (you|your)\b"),
    signal(3, "reports an account or payment the customer does not control",
           r"\b(did ?n[o']t|never|have ?n[o']t) (place|placed|make|made|order|ordered) (this|that|the|any)\b",
           r"\b(hacked|compromised)\b", r"\b(unrecogni[sz]ed|unauthori[sz]ed|unknown) (payment|transaction|charge|order)s?\b",
           r"\blost access\b", r"\bno longer have access\b",
           r"\b(can ?not|can't|unable to) (access|log ?in|sign ?in)\b"),
    signal(5, "tells the assistant to set its rules aside",
           r"\b(ignore|disregard|forget|bypass|override) (all |any |your |the |these |previous |prior |above |earlier )+"
           r"(instructions?|rules?|polic(y|ies)|guidelines?|checks?|limits?|prompts?)\b",
           r"\b(admin|developer|debug|maintenance|god) mode\b", r"\bsystem (notice|prompt|message|override|instruction)s?\b",
           r"\bwithout (further|any) (checks?|verification|approval)\b"),
    signal(5, "claims to be staff, an admin or the system",
           r"\b(this is|i am|i'm) \w+( \w+)? (from|with|of|at) (the |your )?(\w+ )?"
           r"(support|admin|security|fraud|billing|management|operations) (team|staff|desk|department)\b",
           r"\b(i am|i'm|as) (an?|the|your) (admin|administrator)\b", r"\b(i am|i'm) your (manager|supervisor|developer)\b",
           r"\b(supervisor|employee|staff|admin) id\b"),
    signal(5, "says an action is already approved",
           r"\bpre[- ]?(approved|authori[sz]ed)\b", r"\balready (been )?(approved|authori[sz]ed|sanctioned)\b",
           r"\b(approved|authori[sz]ed|sanctioned) by (a |the |your |my )?(supervisor|manager|admin|support|system|team)\b",
           r"\b(i|we)('ve| have) (already )?(approved|authori[sz]ed|sanctioned)\b"),
    signal(5, "contains commands or code meant for the assistant",
           r"<!--", r"-->", r"</?(system|assistant|instructions?)>", r"\bassistant\s*:", rf"\b({TOOL_NAMES})\b",
           r"\b(amount_inr|reason_code|policy_doc_id|idempotency_key)\b"),
    signal(6, "asks for a person",
           r"\b(talk|speak|connect me|transfer me|escalate (this|me)) (to|with) (a |an |the |your )?"
           r"(human|person|manager|supervisor|representative|real agent)\b",
           r"\b(get|give) me (a |the |your )?(human|manager|supervisor|real person)\b", r"\bcall ?back\b",
           r"\b(don'?t|do not) want to (talk|speak|chat) (to|with) a bot\b"),
]


# The support queue each rule's tickets go to. Safety and legal are the urgent ones.
SIGNAL_QUEUE = {1: "safety", 2: "legal", 3: "fraud_or_account", 5: "instructions_to_assistant", 6: "human_requested"}


def escalation_signals(message: str) -> list[Signal]:
    text = " ".join((message or "").replace("\u2019", "'").split())
    return [s for s in ESCALATION_SIGNALS if s.pattern.search(text)]


def broken_rules(tool: str, args: dict, order: dict, history: dict | None) -> list[Rule]:
    return [r for r in POLICY_RULES if r.applies(tool, args) and not r.check(args, order, history)]


def classification_escalation(classification: Classification | None) -> str | None:
    """Returns why a ticket must escalate straight after classify, or None if it may continue."""
    if classification is None:
        return "no_classification"
    if classification.reasoning == PARSE_FAILURE_NOTE:
        return "parse_failure"
    if classification.intent == "out_of_scope":
        return "out_of_scope"
    if classification.confidence < CONFIDENCE_FLOOR:
        return "low_confidence"
    return None


def early_escalation(state: TicketState) -> str | None:
    """Returns why a ticket goes to a person straight after classify, before any policy is read
    or any plan is made, or None if it may continue.

    The message is checked before the classification. Until v8 the signals were only read when
    the plan chose to act, so a legal threat the plan chose to answer never met them. A hosted
    model answered one, and answered a ticket with an instruction hidden in a comment.
    """
    if escalation_signals(state.get("raw_message")):
        return "message_signal"
    return classification_escalation(state.get("classification"))


def plan_outcome(state: TicketState) -> str:
    """The decision the graph will actually follow after plan: answer, act or escalate."""
    plan = state.get("plan")
    if plan is None or state.get("loop_count", 0) > MAX_LOOPS:
        return "escalate"
    return plan.decision
