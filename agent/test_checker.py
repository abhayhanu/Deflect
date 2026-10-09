"""The reply checker when it is a decider. The real client library talks to a small local
server that speaks the same wire format, so nothing here needs a key or the network."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agent import providers
from agent.approvals import MemoryApprovals
from agent.context import RunContext
from agent.guardrails.audit import MemoryAuditLog
from agent.nodes.verify import sentence_claims
from agent.providers import ChatModel, Decider, DeciderError, Question, Usage, checker_name, choose, cost_inr, get_checker
from agent.state import Plan, RetrievedPolicy, ToolCall
from agent.test_graph import NOW, classification, plan, run, tools  # noqa: F401

pytest.importorskip("typesafe_sdk")


class FakeJev:
    """Answers every question as supported, unless its sentence holds one of the doubted
    phrases, which then gets that probability of being unsupported."""

    def __init__(self):
        self.doubted: dict[str, float] = {}
        self.requests: list[dict] = []
        self.status = 200
        self.drop_answers = False
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), **body})
                payload = json.dumps(fake.reply(body)).encode()
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def reply(self, body: dict) -> dict:
        if self.status != 200:
            return {"error": {"message": "the service is down"}}
        answers = {}
        for name, question in body["questions"].items():
            doubt = max([p for phrase, p in self.doubted.items() if phrase in question["instructions"]], default=0.05)
            spread = {"supported": round(1 - doubt, 3), "unsupported": doubt, "not_a_statement": 0.0}
            label = max(spread, key=spread.get)
            answers[name] = {"type": "choice", "choice": label, "confidence": spread[label], "probabilities": spread}
        if self.drop_answers:
            answers.pop(next(iter(answers)))
        return {"model": body["model"], "answers": answers, "usage": {"input_tokens": 1000, "output_tokens": 4}}

    def decider(self) -> Decider:
        from typesafe_sdk import RetryPolicy, TypeSafeClient

        client = TypeSafeClient(api_key="test-key", base_url=self.url, model="jev-latest", retry=RetryPolicy(max_retries=0))
        return Decider("jev", "jev-latest", client)


@pytest.fixture
def jev():
    fake = FakeJev()
    yield fake
    fake.server.shutdown()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFLECT_CHECKER_PROVIDER", "DEFLECT_CHECKER_MODEL", "TYPESAFE_API_KEY", "TYPESAFE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    providers._build_decider.cache_clear()
    yield
    providers._build_decider.cache_clear()


def drafted(reply: str) -> dict:
    policy = RetrievedPolicy(doc_id="pol_lost_transit", title="Lost in Transit", chunk="Share the latest tracking event.", score=0.8)
    return {"plan": Plan(decision="answer", cites=["pol_lost_transit"], rationale="private reasoning"),
            "policies": [policy], "order": {"order_id": "A3107", "status": "shipped"}, "tool_calls": [], "draft": reply}


def test_every_sentence_is_asked_about_in_one_request(jev):
    jev.doubted = {"arrives tomorrow": 0.91}
    usage = Usage()
    reply = "Hi Asha, Your order A3107 has shipped. It arrives tomorrow. Thanks!"
    found, doubt = sentence_claims(drafted(reply), jev.decider(), usage)

    assert found == ["It arrives tomorrow."]
    assert doubt == {"Hi Asha, Your order A3107 has shipped.": 0.05, "It arrives tomorrow.": 0.91}
    assert len(jev.requests) == 1
    sent = jev.requests[0]
    assert sent["path"] == "/v1/systemone" and sent["auth"] == "Bearer test-key" and sent["model"] == "jev-latest"
    assert {"policy_excerpts", "order_record", "actions_taken", "reply"} <= set(sent["state"])
    assert all(q["type"] == "choice" and set(q["criteria"]) == {"supported", "unsupported", "not_a_statement"}
               for q in sent["questions"].values())
    # The plan's reasoning is kept from the checker, whichever kind of checker it is.
    assert "private reasoning" not in json.dumps(sent)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (1, 1000, 4)
    assert usage.cost_inr == pytest.approx(cost_inr(jev.decider(), 1000, 4)) and usage.cost_inr > 0


def test_the_checker_is_shown_what_was_asked_for_and_the_refund_timeline(jev):
    state = drafted("The address on A3107 is now <ADDRESS_1>, Bengaluru. Your refund reaches you in 1 to 3 business days.")
    state["order"] = {"order_id": "A3107", "belongs_to_customer": True, "payment_method": "upi"}
    state["tool_calls"] = [ToolCall(name="update_shipping_address", result={"updated": True}, error=None, authorized_by="policy",
                                    latency_ms=1, called_at=NOW, args={"order_id": "A3107", "new_address": "<ADDRESS_1>",
                                                                       "city": "Bengaluru", "idempotency_key": "T1:x:1"})]
    sentence_claims(state, jev.decider(), Usage())

    sent = jev.requests[0]["state"]
    assert sent["actions_taken"] == [{"action": "update_shipping_address", "result": {"updated": True},
                                      "arguments": {"order_id": "A3107", "new_address": "<ADDRESS_1>", "city": "Bengaluru"}}]
    assert sent["refund_timeline"] == "This order was paid by upi. A refund to it reaches the customer in 1 to 3 business days."


@pytest.mark.parametrize("probability, flagged", [(0.49, False), (0.5, True)])
def test_the_threshold_decides_what_is_sent_back(jev, probability, flagged):
    jev.doubted = {"arrives tomorrow": probability}
    found, _ = sentence_claims(drafted("Your order has shipped. It arrives tomorrow."), jev.decider(), Usage())
    assert bool(found) is flagged


def test_a_long_reply_is_split_across_requests(jev, monkeypatch):
    monkeypatch.setattr(providers, "DECIDER_BATCH", 2)
    questions = {f"s{i}": Question(f"sentence number {i}", {"supported": "yes", "unsupported": "no"}) for i in range(5)}
    answers = choose(jev.decider(), {"reply": "x"}, questions, Usage())
    assert list(answers) == list(questions)
    assert [len(r["questions"]) for r in jev.requests] == [2, 2, 1]


def test_a_failing_service_and_a_missing_answer_both_raise(jev):
    question = {"s0": Question("one sentence here", {"supported": "yes", "unsupported": "no"})}
    jev.drop_answers = True
    with pytest.raises(DeciderError, match="no answer"):
        choose(jev.decider(), {}, question, Usage())
    jev.drop_answers, jev.status = False, 500
    with pytest.raises(DeciderError):
        choose(jev.decider(), {}, question, Usage())


def test_the_checker_is_the_agents_model_until_configured(jev, monkeypatch):
    agent = ChatModel("ollama", "qwen2.5:7b", None)
    assert get_checker(agent) is agent
    assert checker_name(agent) == "ollama:qwen2.5:7b"

    monkeypatch.setenv("DEFLECT_CHECKER_PROVIDER", "jev")
    with pytest.raises(ValueError, match="TYPESAFE_API_KEY"):
        get_checker(agent)

    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setenv("TYPESAFE_BASE_URL", jev.url)
    checker = get_checker(agent)
    assert isinstance(checker, Decider) and (checker.provider, checker.name) == ("jev", "jev-latest")
    assert checker_name(agent) == "jev:jev-latest"


def test_a_chat_model_from_another_provider_can_be_the_checker(monkeypatch):
    monkeypatch.setenv("DEFLECT_CHECKER_PROVIDER", "gemini")
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    checker = get_checker(ChatModel("ollama", "qwen2.5:7b", None))
    assert isinstance(checker, ChatModel) and (checker.provider, checker.name) == ("gemini", "gemini-3.5-flash-lite")


def context(model, tools, checker):
    return RunContext(model=model, check_model=checker, now=NOW, tools=tools, audit=MemoryAuditLog(), approvals=MemoryApprovals())


def test_a_doubted_sentence_goes_back_for_a_rewrite(scripted, tools, jev):
    jev.doubted = {"arrives tomorrow": 0.88}
    model = scripted({"Classification": [classification()], "Plan": [plan(), plan()]},
                     texts=["Your order is on its way. It arrives tomorrow.", "Your order is on its way to <EMAIL_1>."])
    visited, final, ctx = run(model, tools, ctx=context(model, tools, jev.decider()))

    assert visited.count("verify") == 2 and final["terminal_reason"] == "answered"
    check = final["verification"]
    assert (check.verdict, check.checked_by, check.checker_fell_back) == ("pass", "jev:jev-latest", False)
    assert check.sentence_doubt == {"Your order is on its way to <EMAIL_1>.": 0.05}
    assert "ClaimCheck" not in model.client.calls
    assert "It arrives tomorrow." in model.client.structured_prompts[-1]
    # The decider reads the redacted draft, so the real address never leaves the machine.
    assert "asha.rao@example.com" not in json.dumps(jev.requests)
    assert final["reply"].endswith("asha.rao@example.com.")


def test_when_the_decider_is_down_the_agents_model_checks_and_says_so(scripted, tools, jev, caplog):
    jev.status = 500
    model = scripted({"Classification": [classification()], "Plan": [plan()], "ClaimCheck": [{"unsupported_claims": []}]},
                     texts=["Your order is on its way."])
    with caplog.at_level(logging.WARNING, logger="agent.nodes.verify"):
        _, final, _ = run(model, tools, ctx=context(model, tools, jev.decider()))

    check = final["verification"]
    assert (check.verdict, check.checked_by, check.checker_fell_back) == ("pass", "fake:scripted", True)
    assert "ClaimCheck" in model.client.calls
    assert "jev:jev-latest failed" in caplog.text
