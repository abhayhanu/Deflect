"""Tracing through OpenTelemetry.

Every ticket is one trace. Inside it there is a span for each node, one for each tool call and
one for each model call. DEFLECT_TRACE_BACKEND picks where the spans go: langfuse, langsmith,
both, console or none. The backend is only an exporter, so every backend receives exactly the
same spans and swapping one for another is a change to .env.

Nothing leaves this process unredacted. Text is scrubbed when it is put on a span, and every
span is scrubbed again just before it is exported, so a value that slipped past the first pass
is still caught by the second.
"""

import base64
import json
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter, SpanExportResult
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from agent.guardrails.redact import RedactionError, redact

log = logging.getLogger("agent.tracing")

BACKENDS = ("none", "langfuse", "langsmith", "both", "console")
LOCAL_LANGFUSE = "http://localhost:3000"
# A LangSmith key only works on the region the account was created in.
LANGSMITH_REGIONS = {
    "US": "https://api.smith.langchain.com",
    "EU": "https://eu.api.smith.langchain.com",
    "APAC": "https://apac.api.smith.langchain.com",
    "AWS US": "https://aws.api.smith.langchain.com",
}
LANGCHAIN_AUTO_TRACING = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2")
MAX_TEXT = 16_000
REMOVED = "[removed, redaction failed]"

# Each backend reads its own attribute names, so the few that matter are set for both.
OBSERVATION_TYPE = {"ticket": "agent", "node": "span", "guardrail": "guardrail", "tool": "tool", "model": "generation"}
LANGSMITH_KIND = {"ticket": "chain", "node": "chain", "guardrail": "chain", "tool": "tool", "model": "llm"}

_provider: TracerProvider | None = None
_configured = False
_propagator = TraceContextTextMapPropagator()


def scrub(value):
    """Returns the value with every email, phone, card, UPI handle and house address replaced."""
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, default=str, ensure_ascii=False)
    if not isinstance(value, str):
        return value
    try:
        text = redact(value)[0]
    except RedactionError:
        return REMOVED
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + " [cut]"


def clean(attributes: dict) -> dict:
    out = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
            out[key] = [scrub(v) for v in value]
        else:
            out[key] = scrub(value)
    return out


class ScrubbingExporter(SpanExporter):
    """Wraps a real exporter and scrubs names, attributes and events once more on the way out."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner
        self.warned = False

    def export(self, spans) -> SpanExportResult:
        result = self.inner.export([self.scrubbed(s) for s in spans])
        if result is not SpanExportResult.SUCCESS and not self.warned:
            self.warned = True
            log.warning("Traces are not reaching the backend. The ticket is not affected. "
                        "Run python -m agent.tracing to check the key and the endpoint.")
        return result

    def scrubbed(self, span: ReadableSpan) -> ReadableSpan:
        events = [type(e)(e.name, clean(dict(e.attributes or {})), e.timestamp) for e in span.events]
        return ReadableSpan(
            name=scrub(span.name), context=span.context, parent=span.parent, resource=span.resource,
            attributes=clean(dict(span.attributes or {})), events=events, links=span.links, kind=span.kind,
            status=span.status, start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)


def otlp(endpoint: str, headers: dict) -> SpanExporter:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    return OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=10)


def langfuse_target() -> tuple[str, dict]:
    host = (os.getenv("LANGFUSE_HOST") or LOCAL_LANGFUSE).rstrip("/")
    public, secret = os.getenv("LANGFUSE_PUBLIC_KEY"), os.getenv("LANGFUSE_SECRET_KEY")
    if not (public and secret) and host == LOCAL_LANGFUSE:
        # The keys the compose file gives the local project on its first start.
        public, secret = "pk-lf-deflect-local", "sk-lf-deflect-local"
    if not (public and secret):
        raise ValueError("Langfuse needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in .env")
    token = base64.b64encode(f"{public}:{secret}".encode()).decode()
    return f"{host}/api/public/otel/v1/traces", {"Authorization": f"Basic {token}", "x-langfuse-ingestion-version": "4"}


def langsmith_target(base: str | None = None) -> tuple[str, dict]:
    key = os.getenv("LANGSMITH_API_KEY")
    if not key:
        raise ValueError("LangSmith needs LANGSMITH_API_KEY in .env")
    base = (base or os.getenv("LANGSMITH_ENDPOINT") or LANGSMITH_REGIONS["US"]).rstrip("/")
    headers = {"x-api-key": key.strip(), "Langsmith-Project": os.getenv("LANGSMITH_PROJECT") or "deflect"}
    if os.getenv("LANGSMITH_WORKSPACE_ID"):
        # Only needed for a key that belongs to more than one workspace.
        headers["X-Tenant-Id"] = os.getenv("LANGSMITH_WORKSPACE_ID")
    return f"{base}/otel/v1/traces", headers


def targets_for(backend: str) -> list[tuple[str, dict]]:
    if backend not in BACKENDS:
        raise ValueError(f"Unknown DEFLECT_TRACE_BACKEND {backend!r}. Use one of {', '.join(BACKENDS)}.")
    chosen = {"langfuse": [langfuse_target], "langsmith": [langsmith_target],
              "both": [langfuse_target, langsmith_target]}.get(backend, [])
    return [target() for target in chosen]


def exporters_for(backend: str) -> list[SpanExporter]:
    if backend == "console":
        return [ConsoleSpanExporter()]
    return [otlp(endpoint, headers) for endpoint, headers in targets_for(backend)]


def setup(backend: str | None = None, exporters: list[SpanExporter] | None = None, batch: bool = True) -> str:
    """Builds the tracer provider. Tests pass their own exporters. Returns the backend in use."""
    global _provider, _configured
    if _provider is not None:
        _provider.shutdown()
    backend = backend or os.getenv("DEFLECT_TRACE_BACKEND") or "none"
    if exporters is None:
        exporters = exporters_for(backend)
    _configured = True
    warn_about_auto_tracing()
    if not exporters:
        _provider = None
        return "none"

    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    _provider = TracerProvider(resource=Resource.create({"service.name": "deflect"}))
    for exporter in exporters:
        processor = BatchSpanProcessor if batch else SimpleSpanProcessor
        _provider.add_span_processor(processor(ScrubbingExporter(exporter)))
    return backend


def warn_about_auto_tracing() -> None:
    """LangChain's own tracing sends the whole graph state, raw ticket and real reply included.
    Deflect's spans go through redaction and that route does not, so it must stay off."""
    on = [name for name in LANGCHAIN_AUTO_TRACING if (os.getenv(name) or "").lower() in ("1", "true", "yes")]
    if on:
        log.warning("%s is on. That is LangChain's own tracing and it bypasses redaction, so personal details "
                    "would leave this machine. Remove it from .env. DEFLECT_TRACE_BACKEND is all Deflect needs.",
                    ", ".join(on))


def probe(endpoint: str, headers: dict) -> tuple[int | None, str]:
    """Sends an empty export, which records nothing, to see whether the backend accepts the key."""
    import requests

    try:
        reply = requests.post(endpoint, data=b"", headers={**headers, "Content-Type": "application/x-protobuf"}, timeout=10)
    except requests.RequestException as exc:
        return None, f"not reachable: {type(exc).__name__}"
    if reply.status_code in (401, 403):
        return reply.status_code, "the key was rejected"
    if reply.status_code < 300:
        return reply.status_code, "accepted"
    return reply.status_code, "reached, the key was not rejected"


def check(backend: str | None = None, out=print) -> bool:
    """Checks every configured backend and, for LangSmith, finds the region the key belongs to."""
    backend = backend or os.getenv("DEFLECT_TRACE_BACKEND") or "none"
    if backend in ("none", "console"):
        out(f"DEFLECT_TRACE_BACKEND is {backend}, so nothing is sent anywhere.")
        return True
    warn_about_auto_tracing()
    names = {"langfuse": ["langfuse"], "langsmith": ["langsmith"], "both": ["langfuse", "langsmith"]}[backend]
    ok = True
    for name in names:
        try:
            endpoint, headers = langfuse_target() if name == "langfuse" else langsmith_target()
        except ValueError as exc:
            out(f"{name:<10} {exc}")
            ok = False
            continue
        status, verdict = probe(endpoint, headers)
        out(f"{name:<10} {endpoint}  {status or ''} {verdict}")
        if status not in (401, 403) and status is not None:
            continue
        ok = False
        if name == "langfuse":
            out("           Is Langfuse running? Try: docker compose --profile tracing up")
        elif status is None:
            out("           Check the internet connection, and LANGSMITH_ENDPOINT if you set one.")
        else:
            out("           Trying the other LangSmith regions with the same key.")
            for region, base in LANGSMITH_REGIONS.items():
                found, verdict = probe(*langsmith_target(base))
                out(f"           {region:<7} {base}  {found or ''} {verdict}")
                if found is not None and found not in (401, 403):
                    out(f"\nYour account is in the {region} region. Put this line in .env:\n  LANGSMITH_ENDPOINT={base}")
                    break
            else:
                out("\nNo region accepts this key. Create a new API key in LangSmith under Settings, and copy it "
                    "whole. If your account has more than one workspace, also set LANGSMITH_WORKSPACE_ID.")
    return ok


def tracer() -> trace.Tracer:
    if not _configured:
        try:
            setup()
        except ValueError as exc:
            log.warning("Tracing is off: %s", exc)
            setup("none")
    return _provider.get_tracer("deflect") if _provider else trace.NoOpTracer()


def enabled() -> bool:
    tracer()
    return _provider is not None


def flush() -> None:
    if _provider is not None:
        _provider.force_flush()


def trace_url(trace_id: str | None) -> str | None:
    """A link to one ticket's trace, built from DEFLECT_TRACE_URL when that is set.

    The setting is the address your trace backend shows for a trace, with the id replaced by
    trace_id in curly brackets, or by trace_uuid for a backend that writes the id with hyphens.
    """
    template = os.getenv("DEFLECT_TRACE_URL")
    if not (template and trace_id and len(trace_id) == 32):
        return None
    uuid = "-".join((trace_id[:8], trace_id[8:12], trace_id[12:16], trace_id[16:20], trace_id[20:]))
    return template.replace("{trace_id}", trace_id).replace("{trace_uuid}", uuid)


def kind_attributes(kind: str) -> dict:
    return {"langfuse.observation.type": OBSERVATION_TYPE[kind], "langsmith.span.kind": LANGSMITH_KIND[kind]}


@contextmanager
def span(name: str, kind: str, parent=None, **attributes):
    """Opens a span as a child of the current one, or of parent when it is given.

    An interrupt is how a run pauses for a person, so it is recorded as a pause and not an error.
    """
    with tracer().start_as_current_span(name, context=parent, record_exception=False, set_status_on_exception=False,
                                        attributes=clean({**kind_attributes(kind), **attributes})) as current:
        try:
            yield current
        except BaseException as exc:
            if type(exc).__name__ == "GraphInterrupt":
                current.set_attribute("deflect.paused", True)
            else:
                current.record_exception(exc)
                current.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {scrub(str(exc))}"))
            raise


def put(current, **attributes) -> None:
    if current is not None and current.is_recording():
        current.set_attributes(clean(attributes))


def current_context():
    return otel_context.get_current()


@dataclass
class TicketTrace:
    span: object

    def ids(self) -> dict:
        """The trace id and a W3C traceparent for the state, empty when tracing is off."""
        ctx = self.span.get_span_context()
        if not ctx.is_valid:
            return {}
        carrier: dict = {}
        _propagator.inject(carrier, context=trace.set_span_in_context(self.span))
        return {"trace_id": format(ctx.trace_id, "032x"), "trace_parent": carrier["traceparent"]}

    def finish(self, values: dict) -> None:
        c, plan = values.get("classification"), values.get("plan")
        output = values.get("terminal_reason")
        put(self.span, **{
            "deflect.terminal_reason": values.get("terminal_reason"),
            "deflect.intent": c.intent if c else None,
            "deflect.decision": plan.decision if plan else None,
            "deflect.loop_count": values.get("loop_count", 0),
            "deflect.retry_count": values.get("retry_count", 0),
            "deflect.cost_inr": round(values.get("cost_inr", 0.0), 6),
            "deflect.awaiting_approval": bool(values.get("awaiting_approval")),
            "langfuse.observation.output": output, "output.value": output,
        })


@contextmanager
def ticket_trace(ticket_id: str, trace_parent: str | None = None, resumed: bool = False):
    """One run of the graph for one ticket.

    A run resumed after an approval may be in another process hours later. Given the traceparent
    saved in the state, it joins the original trace instead of starting a new one.
    """
    parent = None
    if trace_parent and not trace.get_current_span().is_recording():
        parent = _propagator.extract({"traceparent": trace_parent})
    name = "ticket resumed" if resumed else "ticket"
    meta = {"deflect.ticket_id": ticket_id, "deflect.resumed": resumed, "langfuse.trace.name": "ticket",
            "langfuse.session.id": ticket_id, "langfuse.trace.metadata.ticket_id": ticket_id,
            "langsmith.metadata.ticket_id": ticket_id}
    with span(name, "ticket", parent=parent, **meta) as current:
        yield TicketTrace(current)


def node_attributes(name: str, state: dict, update: dict) -> dict:
    """What a node decided, read from what it wrote. Only small, already redacted values."""
    merged = {**state, **update}
    c, plan = merged.get("classification"), merged.get("plan")
    attrs = {
        "deflect.node": name,
        "deflect.ticket_id": merged.get("ticket_id"),
        "deflect.trace_id": merged.get("trace_id"),
        "deflect.intent": c.intent if c else None,
        "deflect.decision": plan.decision if plan else None,
        "deflect.loop_count": merged.get("loop_count", 0),
        "deflect.retry_count": merged.get("retry_count", 0),
        "deflect.cost_inr": round(merged.get("cost_inr", 0.0), 6),
        "deflect.terminal_reason": update.get("terminal_reason"),
    }
    if "classification" in update and c:
        attrs.update({"deflect.confidence": c.confidence, "deflect.urgency": c.urgency,
                      "deflect.classified_by": update.get("classified_by"),
                      "deflect.classifier_fell_back": update.get("classifier_fell_back")})
    if "plan" in update and plan:
        attrs.update({"deflect.cites": list(plan.cites), "deflect.tool_name": plan.tool_name,
                      "deflect.escalation_reason": plan.escalation_reason, "deflect.rationale": plan.rationale})
    if "policies" in update:
        attrs["deflect.retrieved"] = sorted({p.doc_id for p in update["policies"]})
    if "guardrail" in update:
        g = update["guardrail"]
        attrs.update({"deflect.guardrail": g.outcome, "deflect.guardrail_check": g.check,
                      "deflect.guardrail_detail": g.detail, "deflect.authorized_by": g.authorized_by})
    for call in update.get("tool_calls") or []:
        attrs.update({"deflect.tool_name": call.name, "deflect.authorized_by": call.authorized_by,
                      "deflect.tool_error": call.error})
    if "verification" in update:
        v = update["verification"]
        attrs.update({"deflect.verdict": v.verdict, "deflect.failed_checks": list(v.failed_checks),
                      "deflect.unsupported_claims": list(v.unsupported_claims)})
    if "draft" in update:
        attrs["deflect.draft"] = update["draft"]
    if "redacted_message" in update:
        attrs["langfuse.observation.input"] = update["redacted_message"]
        attrs["input.value"] = update["redacted_message"]
    return attrs


def model_attributes(provider: str, model: str, purpose: str, messages: list) -> dict:
    prompt = "\n\n".join(f"{type(m).__name__}: {m.content}" for m in messages)
    return {"gen_ai.system": provider, "gen_ai.request.model": model, "langfuse.observation.model.name": model,
            "deflect.purpose": purpose, "langfuse.observation.input": prompt, "input.value": prompt}


def model_result(current, tokens_in: int, tokens_out: int, cost: float, output, parsed: bool | None = None) -> None:
    put(current, **{
        "gen_ai.usage.input_tokens": tokens_in, "gen_ai.usage.output_tokens": tokens_out,
        "langfuse.observation.usage_details": {"input": tokens_in, "output": tokens_out},
        "deflect.cost_inr": round(cost, 6), "deflect.parsed": parsed,
        "langfuse.observation.output": output, "output.value": output,
    })


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    sys.exit(0 if check() else 1)
