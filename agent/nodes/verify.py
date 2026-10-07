"""Checks the drafted reply before a customer sees it. Two layers.

The deterministic layer is cheap and catches most problems: every cited policy was really
retrieved, the action that ran is the one the plan chose, a refund cites its own policy, the
reply never claims an action that did not happen, any time it gives for a refund to arrive
matches the payment method, and no template slot like [order_date] is left.

Only if all of that passes does a model read the reply against the sources and list any
statement they do not support. The checker sees the policy excerpts, the order record and the
action result. It never sees the plan's rationale, because a checker that reads the reasoning
tends to agree with it.

The checker can be a chat model, which reads the whole reply and quotes what it doubts, or a
decider, which is asked about one sentence at a time and gives a probability for each. If the
decider cannot be reached the agent's own model checks the reply, and the verdict says so.

A failure goes back for another try, at most twice. A retry after money has moved only
rewrites the reply and never plans again, so a retry can never cause a second action. An
action that does not match its plan or its policy goes straight to a human.
"""

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from agent.context import RunContext
from agent.guardrails.policy import CHECKER_MIN_WORDS, CHECKER_UNSUPPORTED_AT, MAX_VERIFY_RETRIES, REFUND_ARRIVAL
from agent.nodes.draft import cited_policies
from agent.providers import ChatModel, Decider, DeciderError, Question, Usage, call_structured, choose
from agent.state import TicketState, Verification

log = logging.getLogger(__name__)

SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
CONDITIONAL = re.compile(r"\b(if|once|after|when|whenever|as soon as|until|unless|in case|should you)\b")
TEMPLATE_SLOT = re.compile(r"\[[A-Za-z][A-Za-z_ ]{2,40}\]|\{[a-z_]{3,40}\}")

# A claim is a sentence that names the thing and says it happened, is happening or definitely will.
CLAIMS = {
    "refund": (r"\brefund", r"\b(has|have) been (processed|issued|initiated|credited|refunded|approved|sent)\b"
               r"|\b(was|were) (processed|issued|initiated|credited|refunded|approved|sent)\b"
               r"|\bwill (now )?(be )?(process|issu|initiat|credit|refund|send|sent)\w*"
               r"|\b(we|i)('ve| have| are| am|'re|'m)? (now )?(processed|issued|initiated|refunded|processing|issuing"
               r"|initiating|refunding)\b|\b(is|are) (now )?(being )?(processed|issued|initiated)\b|\bfully refunded\b"
               r"|\b(are|am) (now )?(issuing|processing|initiating|refunding)\b"),
    "cancel": (r"\bcancel", r"\b(has|have) been cancel\w*|\b(was|were|is|is now) cancel\w*|\b(we|i)('ve| have)? cancel\w*"
               r"|\bwill (now )?(be )?cancel\w*"),
    "address": (r"\baddress", r"\b(has|have) been (updated|changed)|\b(was|is|is now) (updated|changed)"
                r"|\b(we|i)('ve| have)? (updated|changed)|\bwill (now )?(be )?(updat|chang)\w*"),
    "pickup": (r"\bpick ?up|\breturn label", r"\b(has|have) been (scheduled|booked|arranged|created|raised)"
               r"|\b(was|is) (scheduled|booked|arranged|created)|\b(we|i)('ve| have)? (scheduled|booked|arranged|created)"
               r"|\bwill (now )?(be )?(schedul|book|arrang|creat)\w*"),
    "escalation": (r"\bsupport team|\bhuman|\bspecialist|\bescalat|\bmanager",
                   r"\b(has|have) been (escalated|passed|forwarded|assigned|shared)|\b(we|i)('ve| have)? (escalated|passed"
                   r"|forwarded)|\bwill (be )?(escalat|pass|forward|share|assign|contact|reach|review|look into|get back|call)\w*"
                   r"|\b(is|are) (being )?(escalated|reviewed)"),
}
CLAIM_PATTERNS = {kind: (re.compile(noun), re.compile(verb)) for kind, (noun, verb) in CLAIMS.items()}

# A sentence about money reaching the customer, and the time it gives for that.
ABOUT_ARRIVAL = re.compile(r"\brefund|\bcredit|\breflect|\bmoney\b|\breach(?:es)? (?:you|your)\b"
                           r"|\byour (?:upi|card|wallet|bank)\b|\b(?:upi|wallet|bank) account\b")
NOT_ARRIVAL = re.compile(r"\bpick|\binspect|\bdeliver|\bship|\bdispatch|\bparcel|\bpackage|\breturn label"
                         r"|\breport|\bclaim\b|\braise")
WINDOW = re.compile(r"(\d+)\s*(?:to|-|–|and)\s*(\d+)\s*(?:business |working )?(days?|hours?)"
                    r"|within (?:the next )?(\d+)\s*(?:business |working )?(days?|hours?)")

CHECKER = """You check a reply to a customer before it is sent.

You get the sources: policy excerpts, the order record from our database and the result of
any action we took. Then the reply.

List every statement in the reply that the sources do not support. A statement is a fact, an
amount, a date, a time frame, a promise or an action. A statement is supported when a source
says it, or it follows from a source by simple arithmetic. Time frames and amounts must match
the sources exactly.

These are not statements: greetings, thanks, apologies, the sign off, and repeating what the
customer asked.

Quote each unsupported statement with the exact words from the reply. Return an empty list
when everything is supported."""


# The same rules as the prompt above, as the three labels a decider picks from for one sentence.
SENTENCE_ASK = 'Judge this one sentence from the reply against the sources: "{sentence}"'
SENTENCE_LABELS = {
    "supported": "A source says it, or it follows from a source by simple arithmetic. Any amount, date or time frame "
                 "in it matches the sources exactly.",
    "unsupported": "It gives a fact, an amount, a date, a time frame, a promise or an action that no source says, "
                   "or that a source contradicts.",
    "not_a_statement": "A greeting, thanks, an apology, a sign off, an offer of more help, or a repeat of what the "
                       "customer asked.",
}


class ClaimCheck(BaseModel):
    unsupported_claims: list[str] = Field(default_factory=list)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END.split(text.replace("’", "'")) if s.strip()]


def actions_backed(state: TicketState) -> set[str]:
    """What the reply may say was done: actions from this run, and facts already on the order."""
    done = set()
    for call in state.get("tool_calls") or []:
        if call.error:
            continue
        done |= {"issue_refund": {"refund"}, "cancel_order": {"cancel"}, "update_shipping_address": {"address"},
                 "create_return_label": {"pickup"}}.get(call.name, set())
        if call.name == "cancel_order" and (call.result or {}).get("refund"):
            done.add("refund")
    order = state.get("order") or {}
    if order.get("belongs_to_customer"):
        if order.get("status") == "cancelled":
            done.add("cancel")
        if order.get("refunds") or (order.get("refunded_inr") or 0) > 0:
            done.add("refund")
        if any(s.get("direction") == "return" for s in order.get("shipments", [])):
            done.add("pickup")
    return done


def unbacked_claims(text: str, state: TicketState) -> list[str]:
    """Sentences that say an action happened, or definitely will, when it did not. Conditional
    sentences such as "once the item is picked up, the refund is issued" are left alone."""
    backed = actions_backed(state)
    found = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        if CONDITIONAL.search(lowered):
            continue
        for kind, (noun, verb) in CLAIM_PATTERNS.items():
            if kind not in backed and noun.search(lowered) and verb.search(lowered):
                found.append(sentence)
                break
    return found


def payment_method(state: TicketState) -> str | None:
    for call in reversed(state.get("tool_calls") or []):
        method = (call.result or {}).get("payment_method")
        if not call.error and method:
            return method
    order = state.get("order") or {}
    return order.get("payment_method") if order.get("belongs_to_customer") else None


def windows(sentence: str) -> list[tuple[int, int, str]]:
    found = []
    for m in WINDOW.finditer(sentence):
        if m.group(1):
            found.append((int(m.group(1)), int(m.group(2)), m.group(3).rstrip("s")))
        else:
            found.append((int(m.group(4)), int(m.group(4)), m.group(5).rstrip("s")))
    return found


def matches(window: tuple[int, int, str], arrival) -> bool:
    low, high, unit = window
    if arrival.unit == "hours":
        return (unit == "hour" and (low, high) == (24, 24)) or (unit == "day" and (low, high) == (1, 1))
    return unit == "day" and (low, high) == (arrival.low, arrival.high)


def wrong_timelines(text: str, state: TicketState) -> list[str]:
    """Sentences that give a refund a time to arrive which the payment method's timeline does
    not. The order's method is known, so this is a lookup and not a judgement."""
    arrival = REFUND_ARRIVAL.get(payment_method(state))
    if arrival is None:
        return []
    found = []
    for sentence in sentences(text):
        lowered = sentence.lower()
        if CONDITIONAL.search(lowered) or NOT_ARRIVAL.search(lowered) or not ABOUT_ARRIVAL.search(lowered):
            continue
        if any(not matches(w, arrival) for w in windows(lowered)):
            found.append(f"{sentence} (a refund by {payment_method(state)} arrives {arrival.phrase})")
    return found


def action_problems(state: TicketState) -> list[str]:
    plan = state["plan"]
    if plan.decision != "act":
        return []
    done = [c for c in state.get("tool_calls") or [] if not c.error]
    problems = []
    if not done or done[-1].name != plan.tool_name:
        problems.append("action_mismatch")
    for call in done:
        if call.name == "issue_refund" and call.args.get("policy_doc_id") not in plan.cites:
            problems.append("refund_not_cited")
    return problems


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def in_reply(claim: str, reply: str) -> bool:
    """A small model sometimes lists things the reply never said. Only quotes that are really in
    the reply count."""
    quote, body = normalise(claim), normalise(reply)
    if not quote:
        return False
    if quote in body:
        return True
    words = quote.split()
    present = set(body.split())
    return len(words) >= 3 and sum(w in present for w in words) / len(words) >= 0.8


def action_results(state: TicketState) -> list[dict]:
    return [{"action": c.name, "result": c.result} for c in state.get("tool_calls") or [] if not c.error]


def checker_prompt(state: TicketState) -> list:
    excerpts = "\n\n".join(f"[doc_id: {p.doc_id}]\n{p.chunk}" for p in cited_policies(state))
    results = action_results(state)
    body = f"""Policy excerpts:
{excerpts or "None"}

Order record:
{json.dumps(state.get("order"))}

Actions taken:
{json.dumps(results) if results else "None"}

Reply to check:
{state["draft"]}"""
    return [SystemMessage(CHECKER), HumanMessage(body)]


def actions_with_arguments(state: TicketState) -> list[dict]:
    """What was asked for as well as what came back. The saved arguments hold placeholders, never
    real details. Without them a reply that repeats the new address has nothing to be checked against."""
    return [{"action": c.name, "arguments": {k: v for k, v in c.args.items() if k != "idempotency_key"}, "result": c.result}
            for c in state.get("tool_calls") or [] if not c.error]


def checker_sources(state: TicketState) -> dict:
    """The sources the prompt holds, as the state a decider reads, plus two things the replay of
    v5 showed it needs spelled out: the arguments of each action, and the refund timeline."""
    sources = {
        "policy_excerpts": [{"doc_id": p.doc_id, "text": p.chunk} for p in cited_policies(state)],
        "order_record": state.get("order"),
        "actions_taken": actions_with_arguments(state),
        "reply": state["draft"],
    }
    method = payment_method(state)
    arrival = REFUND_ARRIVAL.get(method)
    if arrival:
        # The same table the timeline rule above just checked the reply against.
        sources["refund_timeline"] = f"This order was paid by {method}. A refund to it reaches the customer {arrival.phrase}."
    return sources


def model_claims(state: TicketState, model: ChatModel, usage: Usage) -> list[str]:
    result = call_structured(model, ClaimCheck, checker_prompt(state), usage)
    if result is None:
        return ["the checker could not read this reply"]
    return [c for c in result.unsupported_claims if in_reply(c, state["draft"])]


def sentence_claims(state: TicketState, decider: Decider, usage: Usage) -> tuple[list[str], dict[str, float]]:
    """Asks about every sentence on its own. Returns the sentences to send back, and how likely
    each sentence is unsupported, so the threshold can be tuned from recorded runs."""
    asked = [s for s in sentences(state["draft"]) if len(s.split()) >= CHECKER_MIN_WORDS]
    if not asked:
        return [], {}
    questions = {f"s{i}": Question(SENTENCE_ASK.format(sentence=s), SENTENCE_LABELS) for i, s in enumerate(asked)}
    answers = choose(decider, checker_sources(state), questions, usage, "ClaimCheck")
    doubt = {s: round(answers[f"s{i}"].probabilities.get("unsupported", 0.0), 3) for i, s in enumerate(asked)}
    return [s for s, p in doubt.items() if p >= CHECKER_UNSUPPORTED_AT], doubt


def named(model: ChatModel | Decider) -> str:
    return f"{model.provider}:{model.name}"


def check_with_model(state: TicketState, ctx: RunContext) -> tuple[list[str], dict]:
    """Returns the unsupported statements and a record of who checked."""
    checker = ctx.checker()
    if not isinstance(checker, Decider):
        return model_claims(state, checker, ctx.usage), {"checked_by": named(checker)}
    try:
        found, doubt = sentence_claims(state, checker, ctx.usage)
        return found, {"checked_by": named(checker), "sentence_doubt": doubt}
    except DeciderError as exc:
        fallback = ctx.chat_model()
        log.warning("The checker %s failed, so %s checks this reply: %s", named(checker), named(fallback), exc)
        return model_claims(state, fallback, ctx.usage), {"checked_by": named(fallback), "checker_fell_back": True}


def verify(state: TicketState, runtime: Runtime[RunContext]) -> dict:
    ctx = runtime.context
    spent_before = ctx.usage.cost_inr
    plan = state["plan"]
    retrieved = {p.doc_id for p in state.get("policies", [])}

    failed, claims = [], []
    if not plan.cites:
        failed.append("no_citation")
        claims.append("the decision cites no policy")
    missing = [c for c in plan.cites if c not in retrieved]
    if missing:
        failed.append("citation_not_retrieved")
        claims.append(f"cites {', '.join(missing)}, which retrieval never returned")
    problems = action_problems(state)
    failed += problems
    unbacked = unbacked_claims(state["draft"], state)
    if unbacked:
        failed.append("claims_unperformed_action")
        claims += [f"{s} (no such action was taken)" for s in unbacked]
    late = wrong_timelines(state["draft"], state)
    if late:
        failed.append("wrong_refund_timeline")
        claims += late
    slots = TEMPLATE_SLOT.findall(state["draft"])
    if slots:
        failed.append("template_slot")
        claims += [f"unfilled template slot {s}" for s in slots]

    checked = {}
    if not failed:
        found, checked = check_with_model(state, ctx)
        if found:
            failed.append("unsupported_claims")
            claims += found

    retries = state.get("retry_count", 0) + (1 if failed else 0)
    if problems:
        verdict = "escalate"
    elif not failed:
        verdict = "pass"
    else:
        verdict = "retry" if retries <= MAX_VERIFY_RETRIES else "escalate"

    check = Verification(
        grounded=not (set(failed) & {"no_citation", "citation_not_retrieved", "unsupported_claims", "claims_unperformed_action",
                                     "wrong_refund_timeline"}),
        action_matches_policy=not problems,
        unsupported_claims=claims,
        verdict=verdict,
        failed_checks=failed,
        **checked,
    )
    update = {"verification": check, "retry_count": retries,
              "cost_inr": state.get("cost_inr", 0.0) + ctx.usage.cost_inr - spent_before}
    if verdict == "escalate":
        update["terminal_reason"] = "escalated_action_mismatch" if problems else "escalated_verify_failed"
    return update
