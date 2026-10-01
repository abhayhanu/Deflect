import pytest
from pydantic import BaseModel

from agent import providers
from agent.providers import ChatModel, Usage, call_structured, cost_inr, get_chat_model


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DEFLECT_PROVIDER", "DEFLECT_MODEL", "DEFLECT_JUDGE_PROVIDER", "DEFLECT_JUDGE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    providers._build.cache_clear()
    yield
    providers._build.cache_clear()


def test_default_is_ollama_with_a_large_context(monkeypatch):
    monkeypatch.setenv("DEFLECT_MODEL", "")
    model = get_chat_model()
    assert (model.provider, model.name) == ("ollama", "qwen2.5:7b")
    assert type(model.client).__name__ == "ChatOllama"
    assert model.client.num_ctx == 8192


@pytest.mark.parametrize("provider, client_class", [("openai", "ChatOpenAI"), ("anthropic", "ChatAnthropic")])
def test_switching_provider_is_only_config(monkeypatch, provider, client_class):
    pytest.importorskip(f"langchain_{provider}", reason="the hosted extra is not installed")
    monkeypatch.setenv("DEFLECT_PROVIDER", provider)
    assert type(get_chat_model().client).__name__ == client_class


def test_judge_is_never_local(monkeypatch):
    monkeypatch.setenv("DEFLECT_JUDGE_PROVIDER", "ollama")
    with pytest.raises(ValueError, match="hosted"):
        get_chat_model("judge")


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("DEFLECT_PROVIDER", "gemini")
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
