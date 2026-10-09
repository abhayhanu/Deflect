import json

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent import tracing
from agent.approvals import decide_and_resume
from agent.conftest import calls_to
from agent.graph import build_graph, memory_checkpointer
from agent.test_graph import CLEAN, ORDER, POLICY_HIT, REFUND, classification, context, plan, refund_plan

EMAIL = "asha.rao@example.com"
PHONE = "98450 12345"
CARD = "4111 1111 1111 1111"
HOUSE = "Flat 12B"
MESSAGE = (f"Please change the address on A3107 to {HOUSE}, Palm Grove, Koramangala, Bengaluru 560095. "
           f"Mail me at {EMAIL} or call {PHONE}. I paid with card {CARD}.")


@pytest.fixture
def spans():
    exporter = InMemorySpanExporter()
    tracing.setup("console", exporters=[exporter], batch=False)
    yield exporter
    tracing.setup("none")


def everything(exported) -> str:
    """Every name, attribute and event of every span, as one string to search."""
    return json.dumps([{"name": s.name, "attributes": dict(s.attributes or {}),
                        "events": [[e.name, dict(e.attributes or {})] for e in s.events]} for s in exported], default=str)


def address_change(scripted, fake_tools):
    placed = {**ORDER, "status": "placed"}
    tools = fake_tools({"search_policy": [POLICY_HIT], "get_order": placed, "get_customer_history": None,
                        "update_shipping_address": {"order_id": "A3107", "updated": True}})
    address_plan = plan("act", cites=["pol_lost_transit"], tool_name="update_shipping_address",
                        tool_args={"order_id": "A3107", "new_address": "<ADDRESS_1>, Palm Grove, Koramangala, Bengaluru 560095",
                                   "city": "Bengaluru"})
    model = scripted({"Classification": [classification("address_change")], "Plan": [address_plan],
                      "ClaimCheck": [CLEAN]}, texts=["Hi, we updated it to <ADDRESS_1>. We will write to <EMAIL_1>."])
    return model, tools


def run_ticket(model, tools, message=MESSAGE, saver=None):
    graph = build_graph(saver or memory_checkpointer())
    ctx = context(model, tools)
    config = {"configurable": {"thread_id": "T1"}}
    state = {"ticket_id": "T1", "raw_message": message, "customer_id": "C_1140", "channel": "chat"}
    with tracing.ticket_trace("T1") as run:
        state.update(run.ids())
        graph.invoke(state, config, context=ctx)
        final = graph.get_state(config).values
        run.finish(final)
    return final, ctx, graph


def test_no_personal_detail_reaches_an_exported_span(scripted, fake_tools, spans):
    model, tools = address_change(scripted, fake_tools)
    final, _, _ = run_ticket(model, tools)

    # The run really used the details: the server got the real address and the customer the real email.
    assert calls_to(tools, "update_shipping_address")[0]["new_address"].startswith(HOUSE)
    assert EMAIL in final["reply"]

    exported = everything(spans.get_finished_spans())
    for secret in (EMAIL, PHONE, CARD, HOUSE, "98450", "4111"):
        assert secret not in exported
    assert "<EMAIL_1>" in exported and "<ADDRESS_1>" in exported


def test_one_trace_with_a_span_per_node_tool_and_model_call(scripted, fake_tools, spans):
    model, tools = address_change(scripted, fake_tools)
    final, ctx, _ = run_ticket(model, tools)
    exported = spans.get_finished_spans()
    names = [s.name for s in exported]

    assert len({s.context.trace_id for s in exported}) == 1
    assert final["trace_id"] == format(exported[0].context.trace_id, "032x")
    # Every audit row points at the trace of the decision behind it.
    assert {row.trace_id for row in ctx.audit.rows} == {final["trace_id"]}
    for node in ("redact", "classify", "retrieve", "plan", "guardrail", "act", "draft", "verify", "respond"):
        assert node in names
    assert {"tool search_policy", "tool get_order", "tool update_shipping_address"} <= set(names)
    assert {"model Classification", "model Plan", "model reply", "model ClaimCheck"} <= set(names)

    by_id = {s.context.span_id: s for s in exported}
    retrieve = next(s for s in exported if s.name == "retrieve")
    search = next(s for s in exported if s.name == "tool search_policy")
    # Retrieve calls its tools from worker threads, and they still land under retrieve.
    assert search.parent.span_id == retrieve.context.span_id
    root = next(s for s in exported if s.name == "ticket")
    assert root.parent is None and root.attributes["deflect.terminal_reason"] == "acted"
    assert by_id[retrieve.parent.span_id] is root

    act = next(s for s in exported if s.name == "tool update_shipping_address")
    assert act.attributes["deflect.authorized_by"] == "policy"
    plan_span = next(s for s in exported if s.name == "plan")
    assert plan_span.attributes["deflect.decision"] == "act" and plan_span.attributes["deflect.loop_count"] == 1
    classify_model = next(s for s in exported if s.name == "model Classification")
    assert classify_model.attributes["gen_ai.usage.input_tokens"] == 100
    assert classify_model.attributes["gen_ai.usage.output_tokens"] == 20
    assert classify_model.attributes["langfuse.observation.type"] == "generation"


def test_the_exporter_scrubs_what_slipped_past_the_first_pass(spans):
    with tracing.tracer().start_as_current_span(f"careless {EMAIL}") as raw:
        raw.set_attribute("note", f"call {PHONE}")
        raw.add_event("said", {"text": CARD})
    exported = everything(spans.get_finished_spans())
    assert EMAIL not in exported and PHONE not in exported and CARD not in exported
    assert "<EMAIL_1>" in exported and "<PHONE_1>" in exported and "<CARD_1>" in exported


def test_a_resumed_approval_joins_the_trace_that_paused(scripted, fake_tools, spans):
    order = {**ORDER, "status": "delivered", "delivered_at": "2026-09-20T06:30:00+00:00", "total_inr": 3499.0}
    tools = fake_tools({"search_policy": [POLICY_HIT], "get_order": order, "get_customer_history": None,
                        "issue_refund": {**REFUND, "amount_inr": 3499.0}})
    model = scripted({"Classification": [classification("refund_request")], "Plan": [refund_plan(3499)],
                      "ClaimCheck": [CLEAN]}, texts=["Your refund of Rs 3,499 is on its way."])
    saver = memory_checkpointer()
    paused, ctx, _ = run_ticket(model, tools, "Lost parcel A3107, refund please", saver)
    assert paused["awaiting_approval"]

    final = decide_and_resume(build_graph(saver), ctx.approvals, paused["approval_id"], True, "priya", ctx)
    assert final["terminal_reason"] == "acted"
    exported = spans.get_finished_spans()
    resumed = next(s for s in exported if s.name == "ticket resumed")
    first = next(s for s in exported if s.name == "ticket")
    assert resumed.context.trace_id == first.context.trace_id
    assert resumed.parent.span_id == first.context.span_id
    assert [s.attributes["deflect.guardrail"] for s in exported if s.name == "guardrail"] == ["approval", "allow"]
    await_span = next(s for s in exported if s.name == "await_approval")
    assert await_span.attributes.get("deflect.paused") is True
    refund = next(s for s in exported if s.name == "tool issue_refund")
    assert refund.attributes["deflect.authorized_by"] == "human" and refund.attributes["deflect.approver_id"] == "priya"


def test_tracing_off_records_nothing_and_adds_no_ids():
    tracing.setup("none")
    with tracing.ticket_trace("T1") as run:
        assert run.ids() == {}
    assert not tracing.enabled()


def test_backends_are_configured_from_env(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    # The local stack works with no keys in .env, anything else must name its own.
    assert tracing.targets_for("langfuse")[0][0].startswith("http://localhost:3000/")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY"):
        tracing.targets_for("langfuse")
    with pytest.raises(ValueError, match="Unknown"):
        tracing.targets_for("jaeger")

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-x")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-y")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000/")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-z")
    # A real .env may name a region, a project or a workspace. The defaults are what is tested here.
    for name in ("LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID"):
        monkeypatch.delenv(name, raising=False)
    (langfuse_url, langfuse_headers), (langsmith_url, langsmith_headers) = tracing.targets_for("both")
    assert langfuse_url == "http://localhost:3000/api/public/otel/v1/traces"
    assert langfuse_headers["Authorization"] == "Basic cGstbGYteDpzay1sZi15"
    assert langsmith_url == "https://api.smith.langchain.com/otel/v1/traces"
    assert langsmith_headers["x-api-key"] == "lsv2-z" and langsmith_headers["Langsmith-Project"] == "deflect"
    assert len(tracing.exporters_for("both")) == 2 and tracing.exporters_for("none") == []


def test_spans_reach_an_otlp_endpoint_with_auth_and_without_pii(scripted, fake_tools, monkeypatch):
    """A local stand in for Langfuse: decodes what the real exporter sends over HTTP."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    received = []

    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            request = ExportTraceServiceRequest()
            request.ParseFromString(body)
            received.append((self.path, dict(self.headers), request))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("LANGFUSE_HOST", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    try:
        tracing.setup("langfuse")
        model, tools = address_change(scripted, fake_tools)
        run_ticket(model, tools)
        tracing.flush()
    finally:
        tracing.setup("none")
        server.shutdown()

    assert received
    path, headers, _ = received[0]
    headers = {k.lower(): v for k, v in headers.items()}
    assert path == "/api/public/otel/v1/traces"
    assert headers["authorization"].startswith("Basic ") and headers["x-langfuse-ingestion-version"] == "4"
    wire = str([r for _, _, r in received])
    assert "ticket" in wire and "tool update_shipping_address" in wire
    for secret in (EMAIL, PHONE, CARD, HOUSE):
        assert secret not in wire


def test_the_check_finds_the_region_a_langsmith_key_belongs_to(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_pt_x")
    monkeypatch.delenv("LANGSMITH_ENDPOINT", raising=False)
    asked = []

    def probe(endpoint, headers):
        asked.append(endpoint)
        return (200, "accepted") if endpoint.startswith("https://eu.") else (403, "the key was rejected")

    monkeypatch.setattr(tracing, "probe", probe)
    lines = []
    assert tracing.check("langsmith", out=lines.append) is False
    assert asked[0] == "https://api.smith.langchain.com/otel/v1/traces"
    assert "LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com" in lines[-1]

    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.api.smith.langchain.com")
    assert tracing.check("langsmith", out=lines.append) is True

    monkeypatch.setattr(tracing, "probe", lambda endpoint, headers: (403, "the key was rejected"))
    assert tracing.check("langsmith", out=lines.append) is False
    assert "No region accepts this key" in lines[-1]


def test_a_workspace_id_is_sent_only_when_set(monkeypatch):
    monkeypatch.setenv("LANGSMITH_API_KEY", " lsv2_pt_x ")
    monkeypatch.delenv("LANGSMITH_WORKSPACE_ID", raising=False)
    _, headers = tracing.langsmith_target()
    assert "X-Tenant-Id" not in headers and headers["x-api-key"] == "lsv2_pt_x"
    monkeypatch.setenv("LANGSMITH_WORKSPACE_ID", "ws-1")
    assert tracing.langsmith_target()[1]["X-Tenant-Id"] == "ws-1"


def test_langchains_own_tracing_is_called_out_because_it_skips_redaction(monkeypatch, caplog):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    tracing.setup("none")
    assert "bypasses redaction" in caplog.text


def test_a_failing_backend_is_reported_once_and_never_raises(caplog):
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class Refusing(SpanExporter):
        def export(self, spans):
            return SpanExportResult.FAILURE

    tracing.setup("console", exporters=[Refusing()], batch=False)
    try:
        for _ in range(3):
            with tracing.ticket_trace("T1"):
                pass
    finally:
        tracing.setup("none")
    assert caplog.text.count("Traces are not reaching the backend") == 1
