"""The classifier when it is a decider. The real client library talks to a small local server
that speaks the same wire format, so nothing here needs a key or the network."""

import json
import logging
from typing import get_args

import pytest

from agent import providers
from agent.approvals import MemoryApprovals
from agent.context import RunContext
from agent.guardrails.audit import MemoryAuditLog
from agent.nodes.classify import INTENT_LABELS, QUESTIONS, SENTIMENT_LABELS, SYSTEM, URGENCY_LABELS
from agent.providers import ChatModel, Decider, classifier_name, get_checker, get_classifier
from agent.state import Classification, Intent
from agent.test_checker import FakeJev
from agent.test_graph import CLEAN, MESSAGE, NOW, classification, plan, run, tools  # noqa: F401

pytest.importorskip("typesafe_sdk")


class FakeClassifier(FakeJev):
    """Picks the label set for each question with the probability set for it, and shares the
    rest evenly between the other labels it was offered."""

    def __init__(self):
        super().__init__()
        self.picks = {"intent": ("order_status", 0.93), "urgency": ("medium", 0.8), "sentiment": ("neutral", 0.9)}

    def reply(self, body: dict) -> dict:
        if self.status != 200:
            return {"error": {"message": "the service is down"}}
        answers = {}
        for name, question in body["questions"].items():
            label, p = self.picks[name]
            others = [other for other in question["criteria"] if other != label]
            spread = {label: p, **{other: round((1 - p) / len(others), 4) for other in others}}
            answers[name] = {"type": "choice", "choice": label, "confidence": p, "probabilities": spread}
        return {"model": body["model"], "answers": answers, "usage": {"input_tokens": 400, "output_tokens": 3}}


@pytest.fixture
def jev():
    fake = FakeClassifier()
    yield fake
    fake.server.shutdown()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFLECT_CLASSIFIER_PROVIDER", "DEFLECT_CLASSIFIER_MODEL", "DEFLECT_CHECKER_PROVIDER", "DEFLECT_CHECKER_MODEL",
                 "TYPESAFE_API_KEY", "TYPESAFE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    providers._build_decider.cache_clear()
    yield
    providers._build_decider.cache_clear()


def context(model, tools, classifier):
    return RunContext(model=model, classify_model=classifier, now=NOW, tools=tools, audit=MemoryAuditLog(),
                      approvals=MemoryApprovals())


def test_the_labels_are_the_prompts_own_definitions():
    assert set(INTENT_LABELS) == set(get_args(Intent))
    for label, says in INTENT_LABELS.items():
        assert f"- {label}: {says}\n" in SYSTEM
    fields = Classification.model_fields
    assert set(URGENCY_LABELS) == set(get_args(fields["urgency"].annotation))
    assert set(SENTIMENT_LABELS) == set(get_args(fields["sentiment"].annotation))
    assert all(says in " ".join(SYSTEM.split()) for says in URGENCY_LABELS.values())


def test_three_questions_go_in_one_request_and_the_chat_model_is_not_asked(scripted, tools, jev):
    model = scripted({"Plan": [plan()], "ClaimCheck": [CLEAN]}, texts=["It is on its way."])
    visited, final, ctx = run(model, tools, ctx=context(model, tools, jev.decider()))

    assert visited[:3] == ["redact", "classify", "retrieve"] and final["terminal_reason"] == "answered"
    c = final["classification"]
    assert (c.intent, c.confidence, c.urgency, c.sentiment, c.order_id) == ("order_status", 0.93, "medium", "neutral", "A3107")
    assert (final["classified_by"], final["classifier_fell_back"]) == ("jev:jev-latest", False)
    assert final["intent_probabilities"]["order_status"] == 0.93 and len(final["intent_probabilities"]) == 8
    assert "Classification" not in model.client.calls

    [sent] = jev.requests
    assert sent["path"] == "/v1/systemone" and sent["model"] == "jev-latest"
    assert sent["state"] == {"channel": "chat", "message": final["redacted_message"]}
    assert set(sent["questions"]) == set(QUESTIONS) == {"intent", "urgency", "sentiment"}
    assert sent["questions"]["intent"]["criteria"] == INTENT_LABELS
    # The decider reads the redacted message, so the real address never leaves the machine.
    assert "asha.rao@example.com" not in json.dumps(sent)
    # What the classifier cost is counted on the ticket like any other model call.
    assert final["cost_inr"] == pytest.approx(ctx.usage.cost_inr) and final["cost_inr"] > 0


def test_a_probability_under_the_floor_sends_the_ticket_to_a_person(scripted, tools, jev):
    jev.picks["intent"] = ("refund_request", 0.45)
    visited, final, _ = run(scripted(), tools, ctx=context(scripted(), tools, jev.decider()))
    assert visited == ["redact", "classify", "escalate", "respond"]
    assert final["terminal_reason"] == "escalated_low_confidence"


def test_out_of_scope_from_the_decider_skips_retrieval(scripted, tools, jev):
    jev.picks["intent"] = ("out_of_scope", 0.97)
    visited, final, _ = run(scripted(), tools, "what is the capital of France", ctx=context(scripted(), tools, jev.decider()))
    assert "retrieve" not in visited and final["terminal_reason"] == "escalated_out_of_scope"
    assert final["classification"].order_id is None


def test_two_order_ids_in_one_message_name_no_order(scripted, tools, jev):
    model = scripted({"Plan": [plan()], "ClaimCheck": [CLEAN]}, texts=["Here is what I found."])
    _, final, _ = run(model, tools, "Where are A3107 and A3258?", ctx=context(model, tools, jev.decider()))
    assert final["classification"].order_id is None


def test_when_the_decider_is_down_the_agents_model_classifies_and_says_so(scripted, tools, jev, caplog):
    jev.status = 500
    model = scripted({"Classification": [classification()], "Plan": [plan()], "ClaimCheck": [CLEAN]}, texts=["On its way."])
    with caplog.at_level(logging.WARNING, logger="agent.nodes.classify"):
        _, final, _ = run(model, tools, ctx=context(model, tools, jev.decider()))

    assert final["classification"].reasoning == "asks where the order is"
    assert (final["classified_by"], final["classifier_fell_back"]) == ("fake:scripted", True)
    assert final["intent_probabilities"] == {}
    assert "jev:jev-latest failed" in caplog.text


def test_a_label_that_was_never_offered_is_a_failure_and_not_an_intent(scripted, tools, jev):
    jev.picks["intent"] = ("billing_dispute", 0.9)
    model = scripted({"Classification": [classification("complaint")], "Plan": [plan()], "ClaimCheck": [CLEAN]}, texts=["Sorry."])
    _, final, _ = run(model, tools, ctx=context(model, tools, jev.decider()))
    assert final["classification"].intent == "complaint" and final["classifier_fell_back"] is True


def test_a_chat_model_classifies_as_before_and_is_named(scripted, tools):
    model = scripted({"Classification": [classification()], "Plan": [plan()], "ClaimCheck": [CLEAN]}, texts=["On its way."])
    _, final, _ = run(model, tools)
    assert (final["classified_by"], final["classifier_fell_back"], final["intent_probabilities"]) == ("fake:scripted", False, {})
    asked = model.client.structured_prompts[0]
    assert asked.startswith(SYSTEM) and asked.endswith(f"Ticket via chat:\n\n{final['redacted_message']}")
    assert MESSAGE not in asked


def test_the_classifier_is_the_agents_model_until_configured(jev, monkeypatch):
    agent = ChatModel("ollama", "qwen2.5:7b", None)
    assert get_classifier(agent) is agent
    assert classifier_name(agent) == "ollama:qwen2.5:7b"

    monkeypatch.setenv("DEFLECT_CLASSIFIER_PROVIDER", "jev")
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        get_classifier(agent)

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", jev.url)
    classifier = get_classifier(agent)
    assert isinstance(classifier, Decider) and (classifier.provider, classifier.name) == ("jev", "jev-latest")
    assert classifier_name(agent) == "jev:jev-latest"
    # The two settings are independent. Turning one on leaves the other with the agent's model.
    assert get_checker(agent) is agent


def test_a_chat_model_from_another_provider_can_be_the_classifier(monkeypatch):
    monkeypatch.setenv("DEFLECT_CLASSIFIER_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    classifier = get_classifier(ChatModel("ollama", "qwen2.5:7b", None))
    assert isinstance(classifier, ChatModel) and (classifier.provider, classifier.name) == ("gemini", "gemini-3.5-flash-lite")
