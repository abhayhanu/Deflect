from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

Intent = Literal[
    "order_status", "refund_request", "return_request",
    "address_change", "cancellation", "complaint",
    "product_question", "out_of_scope",
]
Decision = Literal["answer", "act", "escalate"]
Channel = Literal["email", "chat", "web_form"]

INTENTS = get_args(Intent)
CATEGORY_TAGS = ("straightforward", "edge_case", "adversarial")

TOOL_CLASSES = {
    "get_order": "read",
    "get_customer_history": "read",
    "search_policy": "read",
    "check_shipment": "read",
    "update_shipping_address": "write_low",
    "create_return_label": "write_low",
    "cancel_order": "write_low",
    "issue_refund": "write_high",
    "escalate_to_human": "write_low",
}
ACTION_TOOLS = {"update_shipping_address", "create_return_label", "cancel_order", "issue_refund"}
REFUND_REASON_CODES = ("lost_in_transit", "damaged", "not_as_described", "late_delivery", "goodwill")

# Phase 3 moves this table into agent guardrails, and this file will import it from there.
TOOL_ALLOWLIST: dict[str, set[str]] = {
    "order_status": {"get_order", "search_policy", "check_shipment"},
    "refund_request": {"get_order", "search_policy", "check_shipment", "issue_refund"},
    "return_request": {"get_order", "search_policy", "check_shipment", "create_return_label"},
    "address_change": {"get_order", "search_policy", "check_shipment", "update_shipping_address"},
    "cancellation": {"get_order", "search_policy", "cancel_order", "issue_refund"},
    "complaint": {"get_order", "search_policy", "check_shipment"},
    "product_question": {"search_policy"},
    "out_of_scope": set(),
}


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: Intent
    decision: Decision
    tool_name: str | None = None
    tool_args: dict | None = None
    requires_approval: bool = False
    must_escalate: bool
    required_policy_ids: list[str]
    forbidden_tools: list[str] = Field(default_factory=list)
    reference_reply: str = Field(min_length=1)


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^gold_\d{3}$")
    raw_message: str = Field(min_length=1)
    customer_id: str
    order_id: str | None = None
    channel: Channel
    expected: Expected
    tags: list[str] = Field(min_length=1)

    @property
    def category(self) -> str | None:
        found = [t for t in self.tags if t in CATEGORY_TAGS]
        return found[0] if len(found) == 1 else None
