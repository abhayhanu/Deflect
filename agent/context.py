import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agent import tracing
from agent.approvals import ApprovalStore, PostgresApprovals
from agent.guardrails.audit import AuditEntry, AuditLog, PostgresAuditLog, summarise_read
from agent.guardrails.redact import redact
from agent.mcp_client import MCPClient, ToolOutcome, TransportError, shared_client
from agent.providers import ChatModel, Decider, Usage, get_chat_model, get_checker, get_classifier
from agent.state import TicketState


@dataclass
class RunContext:
    """Objects that live for one run and are never written to a checkpoint.

    The rehydration map holds the real emails and phone numbers behind the placeholders,
    which is exactly why it lives here and not in TicketState.
    """

    model: ChatModel | None = None
    check_model: ChatModel | Decider | None = None
    classify_model: ChatModel | Decider | None = None
    now: datetime | None = None
    rehydration: dict[str, str] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    tools: MCPClient | None = None
    audit: AuditLog | None = None
    approvals: ApprovalStore | None = None

    def chat_model(self) -> ChatModel:
        return self.model or get_chat_model("agent")

    def checker(self) -> ChatModel | Decider:
        # A run that was handed its own model, as every test is, checks and classifies with it too.
        return self.check_model or self.model or get_checker(get_chat_model("agent"))

    def classifier(self) -> ChatModel | Decider:
        return self.classify_model or self.model or get_classifier(get_chat_model("agent"))

    def mcp(self) -> MCPClient:
        if self.tools is None:
            self.tools = shared_client()
        return self.tools

    def audit_log(self) -> AuditLog:
        if self.audit is None:
            self.audit = PostgresAuditLog()
        return self.audit

    def approval_store(self) -> ApprovalStore:
        if self.approvals is None:
            self.approvals = PostgresApprovals()
        return self.approvals

    def tools_for(self, state: TicketState) -> "AuditedTools":
        ticket = state["ticket_id"]
        return AuditedTools(self.mcp(), self.audit_log(), ticket, state.get("trace_id") or ticket)

    def clock(self) -> datetime:
        # Eval runs pin the clock to the seed anchor so time based labels never drift.
        return self.now or datetime.now(timezone.utc)

    def pii_map(self, raw_message: str) -> dict[str, str]:
        # After a resume the map is gone from memory, so rebuild it from the raw message.
        if not self.rehydration:
            self.rehydration.update(redact(raw_message)[1])
        return self.rehydration


class AuditedTools:
    """The MCP client with an audit row written after every call, in one place, so no node can
    forget to write one."""

    def __init__(self, inner: MCPClient, audit: AuditLog, ticket_id: str, trace_id: str):
        self.inner, self.audit = inner, audit
        self.ticket_id, self.trace_id = ticket_id, trace_id
        # Retrieve calls tools from worker threads, which start with no trace of their own.
        self.parent = tracing.current_context()

    def tools(self):
        return self.inner.tools()

    def read_only(self, name: str) -> bool:
        return any(s.name == name and s.read_only for s in self.inner.tools())

    def call(self, name: str, args: dict, authorized_by: str = "policy", approver_id: str | None = None,
             logged_args: dict | None = None) -> ToolOutcome:
        """logged_args is what goes in the log and the trace when args hold real personal details."""
        started = time.perf_counter()
        shown = logged_args or args
        with tracing.span(f"tool {name}", "tool", parent=self.parent, **{
                "deflect.tool_name": name, "deflect.ticket_id": self.ticket_id, "deflect.authorized_by": authorized_by,
                "deflect.approver_id": approver_id, "langfuse.observation.input": shown, "input.value": shown}) as span:
            try:
                outcome = self.inner.call(name, args)
            except TransportError as exc:
                self.write(name, shown, authorized_by, approver_id, started, error=f"transport_error: {exc}")
                tracing.put(span, **{"deflect.tool_error": f"transport_error: {exc}"})
                raise
            result = summarise_read(outcome.result) if self.read_only(name) else outcome.result
            self.write(name, shown, authorized_by, approver_id, started, result=result, error=outcome.error)
            tracing.put(span, **{"deflect.tool_error": outcome.error, "deflect.attempts": outcome.attempts,
                                 "langfuse.observation.output": result, "output.value": result})
        return outcome

    def write(self, name, args, authorized_by, approver_id, started, result=None, error=None) -> None:
        stored = result if isinstance(result, (dict, list)) or result is None else {"value": result}
        self.audit.record(AuditEntry(
            ticket_id=self.ticket_id, trace_id=self.trace_id, tool_name=name, tool_args=args,
            authorized_by=authorized_by, approver_id=approver_id, result=stored, error=error,
            latency_ms=round((time.perf_counter() - started) * 1000),
        ))
