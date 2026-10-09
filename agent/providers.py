"""The only module that knows which model provider is in use.

Everything else asks for a ChatModel and calls call_structured or call_text. Switching
between Ollama and a hosted model is a change to .env, never a change to code.

A Decider is the second kind of model. It never writes text. It reads a state and answers
typed questions about it with a probability for every label, through choose.

Two steps can be handed to a model of their own: the reply checker and the classifier. Each
has its own setting, and each is the agent's model until that setting is filled in.
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

from agent import tracing

load_dotenv()
log = logging.getLogger(__name__)

HOSTED = ("openai", "anthropic", "gemini")
DECIDERS = ("jev",)
DEFAULT_MODELS = {
    "ollama": "qwen2.5:7b",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "gemini": "gemini-3.5-flash-lite",
}
# The judge should be stronger than the agent it grades, so it gets its own default.
JUDGE_MODELS = {"gemini": "gemini-3.8-flash"}
DECIDER_MODELS = {"jev": "jev-latest"}
# Questions sent in one request. A long reply is split so no single request grows without limit.
DECIDER_BATCH = 12

# OpenAI strict JSON schema mode rejects open dict fields like Plan.tool_args, so OpenAI and
# Anthropic use tool calling. Ollama and Gemini constrain their output to the JSON schema.
STRUCTURED_METHOD = {
    "ollama": "json_schema",
    "openai": "function_calling",
    "anthropic": "function_calling",
    "gemini": "json_schema",
}
# What to try when a provider refuses a schema outright, which is not the same as a bad reply.
STRUCTURED_FALLBACK = {"gemini": "function_calling"}
SCHEMA_REFUSED = ("INVALID_ARGUMENT", "response_json_schema", "response_schema")

# US dollars per million tokens, input and output. Prices change, check them before quoting a cost.
# The Gemini prices are the paid tier. On the free tier nothing is charged, and this is what
# the same run would cost once it is.
PRICES_USD = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1-mini": (0.40, 1.60),
    "claude-haiku-4-5": (1.00, 5.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-3.7-flash": (0.75, 3.75),
    "gemini-3.8-flash": (0.75, 3.75),
    "jev": (0.042, 0.0),
}

PARSE_FAILURES: Counter = Counter()
_unpriced_warned: set[str] = set()
_fell_back: set[tuple[str, str]] = set()


def env(name: str, default: str | None = None) -> str | None:
    return os.getenv(name) or default


@dataclass(frozen=True)
class ChatModel:
    provider: str
    name: str
    client: Any


@dataclass(frozen=True)
class Decider:
    """A model that answers typed questions with probabilities and never writes text."""

    provider: str
    name: str
    client: Any


@dataclass(frozen=True)
class Question:
    ask: str
    labels: dict[str, str]


@dataclass(frozen=True)
class Answer:
    label: str
    confidence: float
    probabilities: dict[str, float]


class DeciderError(RuntimeError):
    """The decider could not be reached or gave an answer that cannot be used."""


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

    The agent uses DEFLECT_PROVIDER, which can be ollama, openai, anthropic or gemini.
    The judge uses DEFLECT_JUDGE_PROVIDER and is always a hosted model.
    """
    if role == "judge":
        provider = env("DEFLECT_JUDGE_PROVIDER", "openai")
        if provider not in HOSTED:
            raise ValueError(f"The judge must be a hosted model, got DEFLECT_JUDGE_PROVIDER={provider}")
        name = env("DEFLECT_JUDGE_MODEL", JUDGE_MODELS.get(provider, DEFAULT_MODELS[provider]))
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
        raise ValueError(f"Unknown provider {provider!r}. Use ollama, openai, anthropic or gemini.")
    log.info("Using %s model %s", provider, name)
    return ChatModel(provider, name, client)


def _stage_model(stage: str, default: ChatModel) -> "ChatModel | Decider":
    provider = env(f"DEFLECT_{stage}_PROVIDER")
    if not provider:
        return default
    if provider in DECIDERS:
        return _build_decider(provider, env(f"DEFLECT_{stage}_MODEL", DECIDER_MODELS[provider]))
    return _build(provider, env(f"DEFLECT_{stage}_MODEL", DEFAULT_MODELS.get(provider)))


def _stage_name(stage: str, default: ChatModel | None) -> str:
    provider = env(f"DEFLECT_{stage}_PROVIDER")
    if not provider:
        return f"{default.provider}:{default.name}" if default else "agent model"
    fallback = DECIDER_MODELS.get(provider) or DEFAULT_MODELS.get(provider)
    return f"{provider}:{env(f'DEFLECT_{stage}_MODEL', fallback)}"


def get_checker(default: ChatModel) -> "ChatModel | Decider":
    """The model that checks a drafted reply against its sources.

    Unset means the agent's own model. DEFLECT_CHECKER_PROVIDER can name a decider such as jev,
    or any chat provider, so the checker can be changed without touching the agent's model.
    """
    return _stage_model("CHECKER", default)


def checker_name(default: ChatModel | None = None) -> str:
    """What a run records as its checker, without building a client."""
    return _stage_name("CHECKER", default)


def get_classifier(default: ChatModel) -> "ChatModel | Decider":
    """The model that reads the ticket and picks its intent, urgency and sentiment.

    Unset means the agent's own model. DEFLECT_CLASSIFIER_PROVIDER works the same way as the
    checker's setting, and the two are independent of each other.
    """
    return _stage_model("CLASSIFIER", default)


def classifier_name(default: ChatModel | None = None) -> str:
    """What a run records as its classifier, without building a client."""
    return _stage_name("CLASSIFIER", default)


def configured_names() -> dict[str, str]:
    """The agent's model, its classifier and its checker as the settings name them, without
    building a client. The console shows these beside every run."""
    provider = env("DEFLECT_PROVIDER", "ollama")
    agent = ChatModel(provider, env("DEFLECT_MODEL", DEFAULT_MODELS.get(provider)), None)
    return {"agent": f"{agent.provider}:{agent.name}", "classifier": classifier_name(agent), "checker": checker_name(agent)}


@lru_cache
def _build_decider(provider: str, name: str) -> Decider:
    try:
        from typesafe_sdk import RetryPolicy, TypeSafeClient
    except ImportError as exc:
        raise ImportError(f"{provider} support is not installed. Run: pip install -e .[hosted]") from exc
    if not env("TYPESAFE_API_KEY"):
        raise ValueError(f"{provider} needs TYPESAFE_API_KEY, and it is not set in .env")
    client = TypeSafeClient(model=name, timeout=float(env("DEFLECT_DECIDER_TIMEOUT_S", "10")), retry=RetryPolicy(max_retries=2))
    log.info("Using %s decider %s", provider, name)
    return Decider(provider, name, client)


def _rate_limiter():
    """An optional ceiling on requests per minute, for free tiers that allow only a few."""
    rpm = env("DEFLECT_MAX_RPM")
    if not rpm:
        return None
    from langchain_core.rate_limiters import InMemoryRateLimiter

    return InMemoryRateLimiter(requests_per_second=float(rpm) / 60, check_every_n_seconds=0.1, max_bucket_size=1)


def _gemini_client(name: str, limiter):
    from langchain_google_genai import ChatGoogleGenerativeAI

    try:
        from langchain_google_genai.chat_models import _uses_fixed_sampling_and_disallows_prefill as fixed_sampling
    except ImportError:
        def fixed_sampling(model: str) -> bool:
            return not model.startswith("gemini-2")

    # The newer Gemini models ignore temperature and warn on every call when it is set.
    sampling = {} if fixed_sampling(name) else {"temperature": 0}
    key = env("GOOGLE_API_KEY") or env("GEMINI_API_KEY")
    return ChatGoogleGenerativeAI(model=name, google_api_key=key, timeout=60, max_retries=4, rate_limiter=limiter, **sampling)


def _hosted_client(provider: str, name: str):
    limiter = _rate_limiter()
    try:
        if provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(model=name, temperature=0, timeout=60, max_retries=2, rate_limiter=limiter)
        if provider == "gemini":
            return _gemini_client(name, limiter)
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, temperature=0, timeout=60, max_retries=2, max_tokens=1024, rate_limiter=limiter)
    except ImportError as exc:
        raise ImportError(f"{provider} support is not installed. Run: pip install -e .[hosted]") from exc


def structured_output(model: ChatModel, schema: type[BaseModel]):
    """Wraps the provider specific structured output call.

    include_raw keeps a bad reply from raising, so the caller can count it and retry.
    """
    method = STRUCTURED_METHOD.get(model.provider)
    if (model.provider, schema.__name__) in _fell_back:
        method = STRUCTURED_FALLBACK[model.provider]
    kwargs = {"method": method} if method else {}
    return model.client.with_structured_output(schema, include_raw=True, **kwargs)


def _invoke(model: ChatModel, schema: type[BaseModel], runnable, messages: list[BaseMessage]):
    """Calls the model. If the provider refuses the schema itself, switches to the provider's
    other structured output method, once, and remembers it for this schema."""
    try:
        return runnable, runnable.invoke(messages)
    except Exception as exc:
        fallback = STRUCTURED_FALLBACK.get(model.provider)
        seen = (model.provider, schema.__name__)
        if not fallback or seen in _fell_back or not any(mark in str(exc) for mark in SCHEMA_REFUSED):
            raise
        log.warning("%s refused the %s schema, switching to %s: %s", model.name, schema.__name__, fallback, str(exc)[:200])
        _fell_back.add(seen)
        runnable = structured_output(model, schema)
        return runnable, runnable.invoke(messages)


def call_structured(model: ChatModel, schema: type[BaseModel], messages: list[BaseMessage],
                    usage: Usage, retries: int = 2):
    """Asks for one schema object. Returns None if every attempt fails to validate."""
    runnable = structured_output(model, schema)
    attempt_messages = list(messages)
    log.debug("Prompt for %s: %s", schema.__name__, messages)

    for attempt in range(1, retries + 2):
        with tracing.span(f"model {schema.__name__}", "model", **tracing.model_attributes(
                model.provider, model.name, schema.__name__, attempt_messages), **{"deflect.attempt": attempt}) as span:
            runnable, out = _invoke(model, schema, runnable, attempt_messages)
            usage.structured_calls += 1
            tokens_in, tokens_out, cost = _track(model, out.get("raw"), usage)
            parsed, error = out.get("parsed"), out.get("parsing_error")
            ok = parsed is not None and error is None
            tracing.model_result(span, tokens_in, tokens_out, cost,
                                 parsed.model_dump_json() if ok else _raw_text(out.get("raw")), parsed=ok)
        if ok:
            return parsed

        reason = str(error) if error else "the reply contained no structured output"
        usage.parse_failures += 1
        PARSE_FAILURES[f"{model.provider}:{model.name}:{schema.__name__}"] += 1
        log.warning("Parse failure %d of %d for %s from %s: %s",
                    attempt, retries + 1, schema.__name__, model.name, reason[:300])
        attempt_messages = [*messages, HumanMessage(_retry_note(out.get("raw"), reason))]
    return None


def call_text(model: ChatModel, messages: list[BaseMessage], usage: Usage, purpose: str = "reply") -> str:
    log.debug("Prompt for text reply: %s", messages)
    with tracing.span(f"model {purpose}", "model", **tracing.model_attributes(model.provider, model.name, purpose, messages)) as span:
        reply = model.client.invoke(messages)
        tokens_in, tokens_out, cost = _track(model, reply, usage)
        tracing.model_result(span, tokens_in, tokens_out, cost, reply.text)
    return reply.text.strip()


def choose(decider: Decider, state: dict, questions: dict[str, Question], usage: Usage,
           purpose: str = "choice") -> dict[str, Answer]:
    """Asks every question about the same state and returns one answer per question name.

    Raises DeciderError when the service fails or leaves a question out, so the caller can
    decide what a missing answer means instead of reading it as a yes.
    """
    names = list(questions)
    answers: dict[str, Answer] = {}
    for start in range(0, len(names), DECIDER_BATCH):
        batch = {n: questions[n] for n in names[start:start + DECIDER_BATCH]}
        asked = {n: {"type": "choice", "instructions": q.ask, "criteria": q.labels} for n, q in batch.items()}
        shown = json.dumps({"state": state, "questions": {n: q.ask for n, q in batch.items()}}, default=str)
        with tracing.span(f"model {purpose}", "model", **{
                "gen_ai.system": decider.provider, "gen_ai.request.model": decider.name,
                "langfuse.observation.model.name": decider.name, "deflect.purpose": purpose,
                "deflect.questions": len(batch), "langfuse.observation.input": shown, "input.value": shown}) as span:
            try:
                response = decider.client.system_one(state, asked)
            except Exception as exc:
                raise DeciderError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
            tokens_in = int(response.usage.input_tokens or 0)
            tokens_out = int(response.usage.output_tokens or 0)
            cost = cost_inr(decider, tokens_in, tokens_out)
            usage.calls += 1
            usage.input_tokens += tokens_in
            usage.output_tokens += tokens_out
            usage.cost_inr += cost
            got = {n: Answer(a.choice, a.confidence, dict(a.probabilities)) for n, a in response.choices.items()}
            tracing.model_result(span, tokens_in, tokens_out, cost,
                                 json.dumps({n: {"label": a.label, "confidence": a.confidence} for n, a in got.items()}))
        missing = [n for n in batch if n not in got]
        if missing:
            raise DeciderError(f"no answer for {len(missing)} of {len(batch)} questions")
        answers.update(got)
    return answers


def _raw_text(raw) -> str:
    if raw is None:
        return ""
    calls = getattr(raw, "tool_calls", None)
    return json.dumps(calls[0]["args"]) if calls else raw.text


def _retry_note(raw, reason: str) -> str:
    previous = _raw_text(raw)
    return (
        "Your previous reply could not be used.\n"
        f"Previous reply: {previous[:1500]}\n"
        f"Error: {reason[:800]}\n"
        "Reply again with only the structured output, following the schema exactly."
    )


def _track(model: ChatModel, message, usage: Usage) -> tuple[int, int, float]:
    usage.calls += 1
    meta = getattr(message, "usage_metadata", None) or {}
    tokens_in = int(meta.get("input_tokens") or 0)
    tokens_out = int(meta.get("output_tokens") or 0)
    cost = cost_inr(model, tokens_in, tokens_out)
    usage.input_tokens += tokens_in
    usage.output_tokens += tokens_out
    usage.cost_inr += cost
    return tokens_in, tokens_out, cost


def cost_inr(model: "ChatModel | Decider", tokens_in: int, tokens_out: int) -> float:
    if model.provider not in HOSTED + DECIDERS:
        return 0.0
    price = next((p for prefix, p in PRICES_USD.items() if model.name.startswith(prefix)), None)
    if price is None:
        if model.name not in _unpriced_warned:
            _unpriced_warned.add(model.name)
            log.warning("No price known for %s, its cost is counted as zero. Add it to PRICES_USD.", model.name)
        return 0.0
    usd = (tokens_in * price[0] + tokens_out * price[1]) / 1_000_000
    return usd * float(env("DEFLECT_USD_TO_INR", "88"))
