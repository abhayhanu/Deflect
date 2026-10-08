"""The HTTP face of Deflect: submit a ticket, list what waits for a person, approve or deny it,
and everything the console reads.

A ticket's id is also its LangGraph thread id, so a paused run can always be found again. The
graph is checkpointed in Postgres, so the API process can be stopped while a refund waits for
approval and started again later. The approve endpoint picks the run up from its checkpoint.

The console is a separate React app. In development it runs on its own port and sends its
requests here. Once it has been built, this app also serves the built files, so one process
is the whole product.

Set DEFLECT_API_TOKEN to require a bearer token on every call except the health check and the
public settings. Without it the API is open, which is only fine on your own machine or in the
demo. Set DEFLECT_CLOCK to seed_anchor to pin the clock to the seed time, as the demo data
expects. Set DEFLECT_DEMO to 1 for the public demo: ten tickets are loaded at startup, and a
stricter rate limit and a daily model budget apply.

Run it with uvicorn, pointing at the app object in this module.
"""

import hmac
import logging
import os
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from agent import tracing
from agent.approvals import ApprovalError, ApprovalRequest
from agent.context import RunContext
from agent.providers import configured_names
from api import runview
from api.demo import PRELOADED, Demo, samples
from api.limits import budget, demo_mode, write_limiter
from api.runs import Services, refreshed, resume_ticket, run_ticket, thread
from api.tickets import TicketRow, placeholders_only, status_of, summarise

log = logging.getLogger("api")

ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
CONSOLE_DIR = Path(__file__).resolve().parent.parent / "console" / "dist"


class TicketIn(BaseModel):
    ticket_id: str | None = Field(default=None, pattern=ID_PATTERN)
    raw_message: str = Field(min_length=1, max_length=8000)
    customer_id: str | None = Field(default=None, pattern=r"^C_\d{4}$")
    channel: Literal["email", "chat", "web_form"] = "web_form"


class DecisionIn(BaseModel):
    approver_id: str = Field(pattern=ID_PATTERN)
    note: str | None = Field(default=None, max_length=500)


class ApprovalOut(BaseModel):
    approval_id: str
    ticket_id: str
    tool_name: str
    tool_args: dict
    reason: str | None
    status: str
    approver_id: str | None
    note: str | None
    requested_at: datetime
    decided_at: datetime | None

    @classmethod
    def of(cls, req: ApprovalRequest) -> "ApprovalOut":
        return cls(**{k: getattr(req, k) for k in cls.model_fields})


class TicketOut(BaseModel):
    ticket_id: str
    status: Literal["done", "awaiting_approval", "stopped"]
    terminal_reason: str | None = None
    reply: str | None = None
    approval_id: str | None = None
    tool_calls: list[dict] = []


class TicketSummary(BaseModel):
    """One row of the inbox. The preview is the redacted message, never the customer's own words."""

    ticket_id: str
    channel: str
    customer_id: str | None
    preview: str
    status: Literal["done", "awaiting_approval", "stopped"]
    intent: str | None
    decision: str | None
    terminal_reason: str | None
    cost_inr: float
    latency_ms: int | None
    source: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, row: TicketRow) -> "TicketSummary":
        return cls(**{k: getattr(row, k) for k in cls.model_fields})


class RunView(BaseModel):
    """Everything the console's run view shows for one ticket, with placeholders for personal details."""

    ticket: TicketSummary
    message: str
    steps: list[dict]
    audit: list[dict]
    approval: ApprovalOut | None
    reply: str | None
    trace_id: str | None
    trace_url: str | None
    note: str | None = None


def ticket_view(services: Services, ticket_id: str) -> TicketOut:
    snapshot = services.graph.get_state(thread(ticket_id))
    if not snapshot.values:
        raise HTTPException(404, f"there is no ticket {ticket_id}")
    values = snapshot.values
    status = status_of(snapshot)
    calls = [{"name": c.name, "args": c.args, "result": c.result, "error": c.error,
              "authorized_by": c.authorized_by, "approver_id": c.approver_id} for c in values.get("tool_calls", [])]
    reply = values.get("reply") if status == "done" else None
    if reply and demo_mode():
        # In the demo anyone can read any ticket, so a visitor's own details are never handed back out.
        reply = placeholders_only(reply)
    return TicketOut(ticket_id=ticket_id, status=status, terminal_reason=values.get("terminal_reason"), reply=reply,
                     approval_id=values.get("approval_id") if status == "awaiting_approval" else None,
                     tool_calls=calls)


def run_view(s: Services, ticket_id: str, note: str | None = None) -> RunView:
    config = thread(ticket_id)
    snapshot = s.graph.get_state(config)
    if not snapshot.values:
        raise HTTPException(404, f"there is no ticket {ticket_id}")
    values = snapshot.values
    filed = s.tickets.get(ticket_id)
    row = summarise(ticket_id, snapshot, filed.source if filed else "api", filed.latency_ms if filed else None)
    if filed:
        row.created_at, row.updated_at = filed.created_at, filed.updated_at
    history = list(s.graph.get_state_history(config))[::-1]
    request = s.approvals.get(values["approval_id"]) if values.get("approval_id") else None
    done = status_of(snapshot) == "done"
    return RunView(
        ticket=TicketSummary.of(row), message=values.get("redacted_message") or row.preview,
        steps=runview.steps(history), audit=runview.audit_rows(s.context().audit_log().for_ticket(ticket_id)),
        approval=ApprovalOut.of(request) if request else None,
        reply=placeholders_only(values.get("reply")) if done and values.get("reply") else None,
        trace_id=values.get("trace_id"), trace_url=tracing.trace_url(values.get("trace_id")), note=note,
    )


def caller(request: Request) -> str:
    """Who is asking, for the rate limit. Behind a hosting platform's proxy the real address is
    in a header, and it is only believed when DEFLECT_TRUST_PROXY says a proxy is there. The
    last entry is the one that proxy wrote. Anything before it came from the caller and could
    be made up, which would let one caller look like many."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and os.getenv("DEFLECT_TRUST_PROXY"):
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def security_headers() -> dict[str, str]:
    """Sent with every response. The console loads nothing from anywhere but this server.
    The demo runs inside its host's page, so only there may another site frame it."""
    policy = "default-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'self'"
    if not demo_mode():
        policy += "; frame-ancestors 'self'"
    return {"Content-Security-Policy": policy, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def create_app(open_services: Callable[[], Any]) -> FastAPI:
    """open_services is a context manager that yields Services. Tests pass one with fakes."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not os.getenv("DEFLECT_API_TOKEN") and not demo_mode():
            log.warning("DEFLECT_API_TOKEN is not set, so anyone who can reach this API can approve refunds")
        with open_services() as services:
            app.state.services = services
            app.state.demo = Demo(services) if demo_mode() else None
            if app.state.demo:
                app.state.demo.start()
            yield

    app = FastAPI(title="Deflect", lifespan=lifespan)
    limiter = write_limiter()
    headers = security_headers()

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response

    def services(request: Request) -> Services:
        return request.app.state.services

    def require_token(request: Request) -> None:
        expected = os.getenv("DEFLECT_API_TOKEN")
        if not expected:
            return
        offered = request.headers.get("authorization", "")
        if not (offered.lower().startswith("bearer ") and hmac.compare_digest(offered[7:].strip().encode(), expected.encode())):
            raise HTTPException(401, "a valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    def may_start_a_run(request: Request) -> None:
        """Everything that can cost a model call passes here first."""
        demo = request.app.state.demo
        if demo and demo.loading:
            raise HTTPException(503, "the demo is loading its tickets, try again in a moment", headers={"Retry-After": "15"})
        wait = limiter.wait_for(caller(request))
        if wait:
            raise HTTPException(429, "too many requests, slow down a little", headers={"Retry-After": str(int(wait) + 1)})
        if request.app.state.services.budget.used_up():
            raise HTTPException(429, "today's model budget is used up. Everything already in the inbox can still be opened")

    guarded = [Depends(require_token)]
    costly = [Depends(require_token), Depends(may_start_a_run)]

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/config")
    def config(request: Request) -> dict:
        """What the console needs before it can show anything. It holds no secret."""
        demo = request.app.state.demo
        return {"demo": demo is not None, "token_required": bool(os.getenv("DEFLECT_API_TOKEN")),
                "models": configured_names(), "samples": samples() if demo else [], **runview.limits()}

    @app.get("/tickets", response_model=list[TicketSummary], dependencies=guarded)
    def inbox(limit: int = 100, s: Services = Depends(services)) -> list[TicketSummary]:
        return [TicketSummary.of(refreshed(s, row)) for row in s.tickets.recent(max(1, min(limit, 500)))]

    @app.post("/tickets", response_model=TicketOut, dependencies=costly)
    async def submit(ticket: TicketIn, s: Services = Depends(services)) -> TicketOut:
        ticket_id = ticket.ticket_id or f"T-{uuid.uuid4().hex[:10]}"
        if s.graph.get_state(thread(ticket_id)).values:
            raise HTTPException(409, f"ticket {ticket_id} already exists")
        state = {"ticket_id": ticket_id, "raw_message": ticket.raw_message, "customer_id": ticket.customer_id,
                 "channel": ticket.channel}
        try:
            await run_in_threadpool(run_ticket, s, state)
        except Exception as exc:
            log.exception("Ticket %s stopped before it finished", ticket_id)
            raise HTTPException(502, f"ticket {ticket_id} stopped before it finished: {type(exc).__name__}") from exc
        return ticket_view(s, ticket_id)

    @app.get("/tickets/{ticket_id}", response_model=TicketOut, dependencies=guarded)
    def read_ticket(ticket_id: str, s: Services = Depends(services)) -> TicketOut:
        return ticket_view(s, ticket_id)

    @app.get("/tickets/{ticket_id}/run", response_model=RunView, dependencies=guarded)
    def read_run(ticket_id: str, request: Request, s: Services = Depends(services)) -> RunView:
        demo = request.app.state.demo
        return run_view(s, ticket_id, note=PRELOADED.get(ticket_id) if demo else None)

    @app.get("/approvals", response_model=list[ApprovalOut], dependencies=guarded)
    def pending(s: Services = Depends(services)) -> list[ApprovalOut]:
        return [ApprovalOut.of(r) for r in s.approvals.pending()]

    @app.get("/approvals/{approval_id}", response_model=ApprovalOut, dependencies=guarded)
    def read_approval(approval_id: str, s: Services = Depends(services)) -> ApprovalOut:
        req = s.approvals.get(approval_id)
        if req is None:
            raise HTTPException(404, f"there is no approval request {approval_id}")
        return ApprovalOut.of(req)

    async def decide(approval_id: str, approved: bool, body: DecisionIn, s: Services) -> TicketOut:
        req = s.approvals.get(approval_id)
        if req is None:
            raise HTTPException(404, f"there is no approval request {approval_id}")
        if req.status != "pending":
            raise HTTPException(409, f"{approval_id} was already {req.status} by {req.approver_id}")
        try:
            await run_in_threadpool(resume_ticket, s, approval_id, approved, body.approver_id, body.note, req.thread_id)
        except ApprovalError as exc:
            # Two people decided at the same moment and this one came second.
            raise HTTPException(409, str(exc)) from exc
        return ticket_view(s, req.thread_id)

    @app.post("/approvals/{approval_id}/approve", response_model=TicketOut, dependencies=costly)
    async def approve(approval_id: str, body: DecisionIn, s: Services = Depends(services)) -> TicketOut:
        return await decide(approval_id, True, body, s)

    @app.post("/approvals/{approval_id}/deny", response_model=TicketOut, dependencies=costly)
    async def deny(approval_id: str, body: DecisionIn, s: Services = Depends(services)) -> TicketOut:
        return await decide(approval_id, False, body, s)

    @app.get("/metrics", dependencies=guarded)
    def metrics() -> dict:
        """The recorded eval history, read from the same file a person reads."""
        from evals.history import history

        return history()

    def the_demo(request: Request) -> Demo:
        if request.app.state.demo is None:
            raise HTTPException(404, "this is not the demo")
        return request.app.state.demo

    @app.get("/demo", dependencies=guarded)
    def demo_status(demo: Demo = Depends(the_demo)) -> dict:
        return demo.status()

    @app.post("/demo/reset", status_code=202, dependencies=guarded)
    def demo_reset(demo: Demo = Depends(the_demo)) -> dict:
        status = demo.status()
        if status["reset_in_s"]:
            raise HTTPException(429, "the demo was reset a short while ago", headers={"Retry-After": str(status["reset_in_s"])})
        if demo.services.budget.used_up():
            raise HTTPException(429, "today's model budget is used up, so the demo cannot be loaded again today")
        if not demo.start(reset=True):
            raise HTTPException(409, "the demo is already loading")
        return demo.status()

    if CONSOLE_DIR.is_dir():
        from fastapi.staticfiles import StaticFiles

        # Mounted last, so every route above wins and the console gets whatever is left.
        app.mount("/", StaticFiles(directory=CONSOLE_DIR, html=True), name="console")

    return app


@contextmanager
def live_services():
    """The real thing: MCP server, Postgres checkpoints, Postgres approvals and audit log."""
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from agent.approvals import PostgresApprovals
    from agent.graph import build_graph, checkpoint_serde
    from agent.mcp_client import connect as connect_tools
    from api.tickets import PostgresTickets
    from data.db import app_database_url, connect
    from data.index_policies import ensure_indexed
    from data.queries import fetch_seed_anchor

    pinned = os.getenv("DEFLECT_CLOCK") == "seed_anchor" or demo_mode()
    anchor = None
    if pinned:
        with connect() as conn:
            anchor = fetch_seed_anchor(conn)
    # Fails here, at startup, if Qdrant is down or the policies were never indexed.
    ensure_indexed()
    # And here if the classifier or the reply checker is set to a model with no key, not on the first ticket.
    RunContext().classifier()
    RunContext().checker()
    tools = connect_tools(pin_clock=pinned)
    tools.start()

    def reseed() -> None:
        from data.seed_orders import build_dataset, parse_anchor, write_to_db

        moment = anchor or parse_anchor(os.getenv("DEFLECT_SEED_ANCHOR"))
        write_to_db(build_dataset(moment), moment, reset=True)

    from langgraph.checkpoint.postgres import PostgresSaver

    pool = ConnectionPool(app_database_url(), max_size=5, open=True,
                          kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row})
    try:
        with tools:
            graph = build_graph(PostgresSaver(pool, serde=checkpoint_serde()))
            yield Services(graph=graph, approvals=PostgresApprovals(), tickets=PostgresTickets(), budget=budget(),
                           context=lambda: RunContext(now=anchor, tools=tools), reseed=reseed)
    finally:
        pool.close()


app = create_app(live_services)
