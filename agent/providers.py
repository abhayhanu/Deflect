"""The only module that knows which model provider is in use.

Everything else asks for a ChatModel and calls call_structured or call_text. Switching
between Ollama and a hosted model is a change to .env, never a change to code.
"""

import json
import logging
import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel

load_dotenv()
log = logging.getLogger(__name__)

HOSTED = ("openai", "anthropic")
DEFAULT_MODELS = {
    "ollama": "qwen2.5:7b",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
}

# OpenAI strict JSON schema mode rejects open dict fields like Plan.tool_args, so hosted
# models use tool calling. Ollama constrains its output to the JSON schema directly.
STRUCTURED_METHOD = {
    "ollama": "json_schema",
    "openai": "function_calling",
    "anthropic": "function_calling",
}

# US dollars per million tokens, input and output. Prices change, check them before quoting a cost.
PRICES_USD = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-haiku-4-5": (1.00, 5.00),
}

PARSE_FAILURES: Counter = Counter()
_unpriced_warned: set[str] = set()


def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name) or default


@dataclass(frozen=True)
class ChatModel:
    provider: str
    name: str
    client: Any


@dataclass
class Usage:
    calls: int = 0
    structured_calls: int = 0
    parse_failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_inr: float = 0.0


def get_chat_model(role: Literal["agent", "judge"] = "agent") -> ChatModel:
    """Returns a configured chat model.

    The agent uses DEFLECT_PROVIDER, which can be ollama, openai or anthropic.
    The judge uses DEFLECT_JUDGE_PROVIDER and is always a hosted model.
    """
    if role == "judge":
        provider = env("DEFLECT_JUDGE_PROVIDER", "openai")
        if provider not in HOSTED:
            raise ValueError(f"The judge must be a hosted model, got DEFLECT_JUDGE_PROVIDER={provider}")
        name = env("DEFLECT_JUDGE_MODEL", DEFAULT_MODELS[provider])
    else:
        provider = env("DEFLECT_PROVIDER", "ollama")
        name = env("DEFLECT_MODEL", DEFAULT_MODELS.get(provider))
    return _build(provider, name)


@lru_cache
def _build(provider: str, name: str) -> ChatModel:
    if provider == "ollama":
        from langchain_ollama import ChatOllama

        client = ChatOllama(
            model=name,
            base_url=env("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0,
            # Ollama's default context is small enough to silently cut off the policy excerpts.
            num_ctx=int(env("DEFLECT_OLLAMA_NUM_CTX", "8192")),
        )
    elif provider in HOSTED:
        client = _hosted_client(provider, name)
    else:
        raise ValueError(f"Unknown provider {provider!r}. Use ollama, openai or anthropic.")
    log.info("Using %s model %s", provider, name)
    return ChatModel(provider, name, client)


def _hosted_client(provider: str, name: str):
    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=name, temperature=0, timeout=60, max_retries=2)
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, temperature=0, timeout=60, max_retries=2, max_tokens=1024)
    except ImportError as exc:
        raise ImportError(f"{provider} support is not installed. Run: pip install -e .[hosted]") from exc


def structured_output(model: ChatModel, schema: type[BaseModel]):
    """Wraps the provider specific structured output call.

    include_raw keeps a bad reply from raising, so the caller can count it and retry.
    """
    method = STRUCTURED_METHOD.get(model.provider)
    kwargs = {"method": method} if method else {}
    return model.client.with_structured_output(schema, include_raw=True, **kwargs)


def call_structured(model: ChatModel, schema: type[BaseModel], messages: list[BaseMessage],
                    usage: Usage, retries: int = 2):
    """Asks for one schema object. Returns None if every attempt fails to validate."""
    runnable = structured_output(model, schema)
    attempt_messages = list(messages)
    log.debug("Prompt for %s: %s", schema.__name__, messages)

    for attempt in range(1, retries + 2):
        out = runnable.invoke(attempt_messages)
        usage.structured_calls += 1
        _track(model, out.get("raw"), usage)

        parsed, error = out.get("parsed"), out.get("parsing_error")
        if parsed is not None and error is None:
            return parsed

        reason = str(error) if error else "the reply contained no structured output"
        usage.parse_failures += 1
        PARSE_FAILURES[f"{model.provider}:{model.name}:{schema.__name__}"] += 1
        log.warning("Parse failure %d of %d for %s from %s: %s",
                    attempt, retries + 1, schema.__name__, model.name, reason[:300])
        attempt_messages = [*messages, HumanMessage(_retry_note(out.get("raw"), reason))]
    return None


def call_text(model: ChatModel, messages: list[BaseMessage], usage: Usage) -> str:
    log.debug("Prompt for text reply: %s", messages)
    reply = model.client.invoke(messages)
    _track(model, reply, usage)
    return reply.text.strip()


def _retry_note(raw, reason: str) -> str:
    previous = ""
    if raw is not None:
        calls = getattr(raw, "tool_calls", None)
        previous = json.dumps(calls[0]["args"]) if calls else raw.text
    return (
        "Your previous reply could not be used.\n"
        f"Previous reply: {previous[:1500]}\n"
        f"Error: {reason[:800]}\n"
        "Reply again with only the structured output, following the schema exactly."
    )


def _track(model: ChatModel, message, usage: Usage) -> None:
    usage.calls += 1
    meta = getattr(message, "usage_metadata", None) or {}
    tokens_in = int(meta.get("input_tokens") or 0)
    tokens_out = int(meta.get("output_tokens") or 0)
    usage.input_tokens += tokens_in
    usage.output_tokens += tokens_out
    usage.cost_inr += cost_inr(model, tokens_in, tokens_out)


def cost_inr(model: ChatModel, tokens_in: int, tokens_out: int) -> float:
    if model.provider not in HOSTED:
        return 0.0
    price = next((p for prefix, p in PRICES_USD.items() if model.name.startswith(prefix)), None)
    if price is None:
        if model.name not in _unpriced_warned:
            _unpriced_warned.add(model.name)
            log.warning("No price known for %s, its cost is counted as zero. Add it to PRICES_USD.", model.name)
        return 0.0
    usd = (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000
    return usd * float(env("DEFLECT_USD_TO_INR", "88"))
