import pytest
from pydantic import BaseModel

from agent import providers
from agent.providers import ChatModel, Usage, call_structured, cost_inr, get_chat_model


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFLECT_PROVIDER", "DEFLECT_MODEL", "DEFLECT_JUDGE_PROVIDER", "DEFLECT_JUDGE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEFLECT_MAX_RPM", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("GOOGLE_API_KEY", "test")
    providers._build.cache_clear()
    providers._fell_back.clear()
    yield
    providers._build.cache_clear()
    providers._fell_back.clear()


def test_default_is_ollama_with_a_large_context(monkeypatch):
    monkeypatch.setenv("DEFLECT_MODEL", "")
    model = get_chat_model()
    assert (model.provider, model.name) == ("ollama", "qwen2.5:7b")
    assert type(model.client).__name__ == "ChatOllama"
    assert model.client.num_ctx == 8192


@pytest.mark.parametrize("provider, module, client_class", [
    ("openai", "langchain_openai", "ChatOpenAI"),
    ("anthropic", "langchain_anthropic", "ChatAnthropic"),
    ("gemini", "langchain_google_genai", "ChatGoogleGenerativeAI"),
])
def test_switching_provider_is_only_config(monkeypatch, provider, module, client_class):
    pytest.importorskip(module, reason="the hosted extra is not installed")
    monkeypatch.setenv("DEFLECT_PROVIDER", provider)
    assert type(get_chat_model().client).__name__ == client_class


def test_the_gemini_judge_defaults_to_a_stronger_model_than_the_agent(monkeypatch):
    pytest.importorskip("langchain_google_genai", reason="the hosted extra is not installed")
    monkeypatch.setenv("DEFLECT_PROVIDER", "gemini")
    monkeypatch.setenv("DEFLECT_JUDGE_PROVIDER", "gemini")
    agent, judge = get_chat_model(), get_chat_model("judge")
    assert (agent.name, judge.name) == ("gemini-3.5-flash-lite", "gemini-3.8-flash")
    assert cost_inr(agent, 1_000_000, 0) > 0 and cost_inr(judge, 1_000_000, 0) > 0


def test_a_request_ceiling_is_attached_only_when_asked_for(monkeypatch):
    pytest.importorskip("langchain_google_genai", reason="the hosted extra is not installed")
    monkeypatch.setenv("DEFLECT_PROVIDER", "gemini")
    assert get_chat_model().client.rate_limiter is None
    providers._build.cache_clear()
    monkeypatch.setenv("DEFLECT_MAX_RPM", "12")
    assert get_chat_model().client.rate_limiter.requests_per_second == pytest.approx(0.2)


def test_judge_is_never_local(monkeypatch):
    monkeypatch.setenv("DEFLECT_JUDGE_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="hosted"):
        get_chat_model("judge")


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("DEFLECT_PROVIDER", "mistral")
    with pytest.raises(ValueError, match="Unknown provider"):
        get_chat_model()


def test_cost_is_zero_locally_and_priced_when_hosted(monkeypatch):
    monkeypatch.setenv("DEFLECT_USD_TO_INR", "90")
    assert cost_inr(ChatModel("ollama", "qwen2.5:7b", None), 10_000, 10_000) == 0
    assert cost_inr(ChatModel("openai", "gpt-4o-mini", None), 1_000_000, 0) == pytest.approx(0.15 * 90)


class Answer(BaseModel):
    value: int


def test_parse_failures_are_counted_per_run_and_per_model(scripted):
    before = sum(providers.PARSE_FAILURES.values())
    usage = Usage()
    result = call_structured(scripted({"Answer": ["{", '{"value": "x"}', '{"value": 3}']}), Answer, [], usage)
    assert result == Answer(value=3)
    assert (usage.structured_calls, usage.parse_failures) == (3, 2)
    assert sum(providers.PARSE_FAILURES.values()) - before == 2
    assert usage.input_tokens == 300


class PickyClient:
    """Refuses the schema on the first structured output method, like an API answering 400."""

    def __init__(self):
        self.methods = []

    def with_structured_output(self, schema, include_raw=True, method=None):
        self.methods.append(method)
        client = self

        class Runnable:
            def invoke(self, messages):
                if method == "json_schema":
                    raise RuntimeError("400 INVALID_ARGUMENT: response_json_schema is not supported")
                return {"raw": None, "parsed": schema(value=7), "parsing_error": None}

        return Runnable()


def test_a_refused_schema_switches_method_once_and_remembers():
    model = ChatModel("gemini", "gemini-3.5-flash-lite", PickyClient())
    usage = Usage()
    assert call_structured(model, Answer, [], usage) == Answer(value=7)
    assert model.client.methods == ["json_schema", "function_calling"]
    assert call_structured(model, Answer, [], usage) == Answer(value=7)
    assert model.client.methods[-1] == "function_calling" and usage.parse_failures == 0


def test_other_errors_are_not_mistaken_for_a_refused_schema():
    class RateLimited(PickyClient):
        def with_structured_output(self, schema, include_raw=True, method=None):
            class Runnable:
                def invoke(self, messages):
                    raise RuntimeError("429 RESOURCE_EXHAUSTED")
            return Runnable()

    with pytest.raises(RuntimeError, match="429"):
        call_structured(ChatModel("gemini", "gemini-3.5-flash-lite", RateLimited()), Answer, [], Usage())
