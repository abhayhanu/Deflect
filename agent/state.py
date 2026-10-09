from datetime import datetime
from operator import add
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Intent = Literal[
    "order_status", "refund_request", "return_request",
    "address_change", "cancellation", "complaint",
    "product_question", "out_of_scope",
]

Decision = Literal["answer", "act", "escalate"]


class Classification(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    urgency: Literal["low", "medium", "high"]
    sentiment: Literal["neutral", "frustrated", "angry"]
    order_id: str | None = None
    reasoning: str


class RetrievedPolicy(BaseModel):
    doc_id: str
    title: str
    chunk: str
    score: float
    # True when the section is always shown to the plan and the search did not rank it.
    pinned: bool = False


class Plan(BaseModel):
    # A model fills these in from the top down and cannot go back. The rationale comes first so
    # the decision is written after the reasoning, not before it. Until v10 it came fifth.
    rationale: str
    decision: Decision
    tool_name: str | None = None
    tool_args: dict | None = None
    cites: list[str]
    escalation_reason: str | None = None


class ToolCall(BaseModel):
    name: str
    args: dict
    result: dict | None
    error: str | None
    authorized_by: Literal["policy", "human", "denied"]
    latency_ms: int
    called_at: datetime
    approver_id: str | None = None


class GuardrailVerdict(BaseModel):
    outcome: Literal["allow", "approval", "deny"]
    check: str | None = None
    detail: str | None = None
    action_key: str | None = None
    authorized_by: Literal["policy", "human"] = "policy"


class HumanDecision(BaseModel):
    approval_id: str
    approved: bool
    approver_id: str
    note: str | None = None
    decided_at: datetime


class Verification(BaseModel):
    grounded: bool
    action_matches_policy: bool
    unsupported_claims: list[str]
    verdict: Literal["pass", "retry", "escalate"]
    failed_checks: list[str] = Field(default_factory=list)
    # Which model read the reply. Empty when a rule failed first and no model was asked.
    checked_by: str = ""
    checker_fell_back: bool = False
    # How likely each sentence is unsupported, when the checker gives a probability.
    sentence_doubt: dict[str, float] = Field(default_factory=dict)


class TicketState(TypedDict, total=False):
    # Inputs, written once by the caller
    ticket_id: str
    raw_message: str
    customer_id: str
    channel: Literal["email", "chat", "web_form"]
    received_at: datetime

    # Written by the nodes
    redacted_message: str
    classification: Classification
    # Who classified. Kept out of Classification, which is the schema a chat model fills in.
    classified_by: str
    classifier_fell_back: bool
    # How likely each intent is, when the classifier gives a probability.
    intent_probabilities: dict[str, float]
    policies: list[RetrievedPolicy]
    order: dict | None
    customer_history: dict | None
    plan: Plan
    guardrail: GuardrailVerdict
    human_decision: HumanDecision | None
    tool_calls: Annotated[list[ToolCall], add]
    draft: str
    verification: Verification
    escalation: dict | None
    reply: str

    # Control fields, written by several nodes
    loop_count: int
    retry_count: int
    awaiting_approval: bool
    approval_id: str | None
    terminal_reason: str | None
    cost_inr: float
    trace_id: str
    trace_parent: str
