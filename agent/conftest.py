import json

import pytest
from langchain_core.messages import AIMessage

from agent.mcp_client import ToolOutcome, ToolSpec
from agent.providers import ChatModel

USAGE = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}


class ScriptedClient:
    """Stands in for a chat model. Each schema gets its own queue of replies.

    A reply can be a dict, a raw string that may not parse, or an exception to raise.
    """

    def __init__(self, structured: dict[str, list] | None = None, texts: list | None = None):
        self.structured = {k: list(v) for k, v in (structured or {}).items()}
        self.texts = list(texts or [])
        self.calls: list[str] = []
        self.texts_seen: list[str] = []
        self.structured_prompts: list[str] = []

    def with_structured_output(self, schema, include_raw=True, **kwargs):
        return ScriptedStructured(self, schema)

    def invoke(self, messages):
        self.calls.append("text")
        self.texts_seen.append("\n".join(str(m.content) for m in messages))
        return AIMessage(content=self.texts.pop(0), usage_metadata=USAGE)


class ScriptedStructured:
    def __init__(self, client: ScriptedClient, schema):
        self.client, self.schema = client, schema

    def invoke(self, messages):
        name = self.schema.__name__
        self.client.calls.append(name)
        self.client.structured_prompts.append("\n".join(str(m.content) for m in messages))
        reply = self.client.structured[name].pop(0)
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        raw = AIMessage(content=text, usage_metadata=USAGE)
        try:
            return {"raw": raw, "parsed": self.schema.model_validate_json(text), "parsing_error": None}
        except Exception as exc:
            return {"raw": raw, "parsed": None, "parsing_error": exc}


@pytest.fixture
def scripted():
    def make(structured=None, texts=None) -> ChatModel:
        return ChatModel("fake", "scripted", ScriptedClient(structured, texts))
    return make


def spec(name: str, read_only: bool, properties: dict | None = None, optional: tuple = ()) -> ToolSpec:
    props = {**(properties or {})}
    required = [k for k in props if k not in optional]
    if not read_only:
        props["idempotency_key"] = {"type": "string"}
        required.append("idempotency_key")
    return ToolSpec(name, f"{name} tool.", {"type": "object", "properties": props, "required": required}, read_only)


ORDER_ID = {"type": "string", "pattern": "^A\\d{4,6}$"}
TOOL_SPECS = [
    spec("get_order", True, {"order_id": ORDER_ID}),
    spec("get_customer_history", True, {"customer_id": {"type": "string"}}),
    spec("search_policy", True, {"query": {"type": "string"}, "intent": {"type": "string"}, "top_k": {"type": "integer"}},
         optional=("intent", "top_k")),
    spec("check_shipment", True, {"order_id": ORDER_ID}),
    spec("update_shipping_address", False, {"order_id": ORDER_ID, "new_address": {"type": "string", "minLength": 5},
                                            "city": {"type": "string"}}),
    spec("create_return_label", False, {"order_id": ORDER_ID,
                                        "reason": {"enum": ["change_of_mind", "damaged", "not_as_described"]}},
         optional=("reason",)),
    spec("cancel_order", False, {"order_id": ORDER_ID}),
    spec("issue_refund", False, {"order_id": ORDER_ID,
                                 "amount_inr": {"type": "number", "exclusiveMinimum": 0},
                                 "reason_code": {"enum": ["lost_in_transit", "damaged", "not_as_described",
                                                          "late_delivery", "goodwill"]},
                                 "policy_doc_id": {"type": "string", "pattern": "^pol_[a-z_]{2,40}$"}}),
    spec("escalate_to_human", False, {"ticket_id": {"type": "string"}, "reason": {"type": "string"},
                                      "summary": {"type": "string"}, "order_id": ORDER_ID,
                                      "priority": {"enum": ["normal", "urgent"]}}, optional=("order_id", "priority")),
]


class FakeTools:
    """Stands in for the MCP server. A result can be a value, a function of the args, a ready
    ToolOutcome, or an exception to raise."""

    def __init__(self, results: dict | None = None):
        self.results = dict(results or {})
        self.calls: list[tuple[str, dict]] = []

    def tools(self):
        return TOOL_SPECS

    def call(self, name: str, args: dict) -> ToolOutcome:
        self.calls.append((name, args))
        value = self.results.get(name)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, ToolOutcome):
            return value
        return ToolOutcome(result=value(args) if callable(value) else value)


@pytest.fixture
def fake_tools():
    return FakeTools


def calls_to(tools: FakeTools, name: str) -> list[dict]:
    return [args for called, args in tools.calls if called == name]
