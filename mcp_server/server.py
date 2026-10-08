"""The Deflect support MCP server.

It runs over stdio, which is how the agent and Claude Desktop start it, or over HTTP, where
every request needs a bearer token. The README shows both commands.

MCP_HTTP_TOKEN may call every tool. MCP_HTTP_READONLY_TOKEN, meant for a public demo, only
sees and calls the read tools.
"""

import argparse
import contextlib
import hmac
import inspect
import json
import logging
import os
import sys

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from mcp_server import __version__
from mcp_server.tools import actions, orders, policy

log = logging.getLogger("mcp_server")

INSTRUCTIONS = """Tools for the support team of an Indian online shop. Amounts are in rupees.
Read tools look up orders, customers, shipments and policies. Write tools refund, cancel,
change a delivery address, book a return pickup or hand a ticket to a human. Every write
needs an idempotency_key that is unique to that one intended action."""

TOOLS = [
    (orders.get_order, "read"),
    (orders.get_customer_history, "read"),
    (policy.search_policy, "read"),
    (orders.check_shipment, "read"),
    (actions.update_shipping_address, "write_low"),
    (actions.create_return_label, "write_low"),
    (actions.cancel_order, "write_low"),
    (actions.issue_refund, "write_high"),
    (actions.escalate_to_human, "write_low"),
]
READ_TOOLS = {fn.__name__ for fn, risk in TOOLS if risk == "read"}
CANNOT_UNDO = {"update_shipping_address", "cancel_order", "issue_refund"}


def annotations(name: str, risk: str) -> ToolAnnotations:
    if risk == "read":
        return ToolAnnotations(readOnlyHint=True, openWorldHint=False)
    return ToolAnnotations(readOnlyHint=False, destructiveHint=name in CANNOT_UNDO, idempotentHint=True, openWorldHint=False)


def access_of(request) -> str:
    """full over stdio, where whoever started the process owns it. Over HTTP it is whatever the
    token gate decided, and read only if the gate left nothing behind."""
    if request is None:
        return "full"
    return getattr(getattr(request, "state", None), "access", None) or "read"


class ReadOnlyScope:
    """Hides and blocks the write tools for callers holding the read only token."""

    async def __call__(self, ctx, call_next):
        if access_of(ctx.request) == "full":
            return await call_next(ctx)
        if ctx.method == "tools/call" and (ctx.params or {}).get("name") not in READ_TOOLS:
            log.warning("Refused %s for a read only token", (ctx.params or {}).get("name"))
            return CallToolResult(content=[TextContent(type="text", text="forbidden: this token may only call read tools")],
                                  is_error=True)
        result = await call_next(ctx)
        if ctx.method == "tools/list":
            listing = listing_of(result)
            result = {**listing, "tools": [t for t in listing["tools"] if t["name"] in READ_TOOLS]}
        return result


def listing_of(result) -> dict:
    return result if isinstance(result, dict) else result.model_dump(by_alias=True, exclude_none=True)


class StrictArguments:
    """Refuses a call that carries an argument the tool does not declare.

    The SDK quietly drops unknown arguments, so a caller that invents one, such as a reason on
    cancel_order, would believe it was used. It is refused instead, and every published schema
    says additionalProperties is false, so clients can see the rule up front."""

    def __init__(self, declared: dict[str, set[str]]):
        self.declared = declared

    async def __call__(self, ctx, call_next):
        if ctx.method == "tools/call":
            params = ctx.params or {}
            name, given = params.get("name"), set((params.get("arguments") or {}).keys())
            extra = sorted(given - self.declared.get(name, given))
            if extra:
                log.warning("Refused %s with undeclared arguments %s", name, extra)
                text = f"unknown_argument: {name} has no argument called {', '.join(extra)}, nothing was done"
                return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
        result = await call_next(ctx)
        if ctx.method == "tools/list":
            listing = listing_of(result)
            tools = [{**t, "inputSchema": {**t["inputSchema"], "additionalProperties": False}} for t in listing["tools"]]
            result = {**listing, "tools": tools}
        return result


class BearerGate:
    """Plain ASGI middleware in front of the HTTP app. No token, no entry."""

    def __init__(self, app, tokens: dict[str, str]):
        self.app = app
        self.tokens = {token: access for token, access in tokens.items() if token}

    def access_for(self, header: str) -> str | None:
        if not header.lower().startswith("bearer "):
            return None
        offered = header[7:].strip()
        for token, access in self.tokens.items():
            if hmac.compare_digest(offered.encode(), token.encode()):
                return access
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        header = dict(scope.get("headers") or []).get(b"authorization", b"").decode("latin-1")
        access = self.access_for(header)
        if access is None:
            body = json.dumps({"error": "unauthorized"}).encode()
            await send({"type": "http.response.start", "status": 401, "headers": [
                (b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        scope.setdefault("state", {})["access"] = access
        await self.app(scope, receive, send)


def build_server(log_level: str = "WARNING") -> MCPServer:
    server = MCPServer("deflect-support", instructions=INSTRUCTIONS, version=__version__, log_level=log_level)
    for fn, risk in TOOLS:
        server.add_tool(fn, annotations=annotations(fn.__name__, risk), meta={"deflect/risk": risk})
    server.middleware.append(StrictArguments({fn.__name__: set(inspect.signature(fn).parameters) for fn, _ in TOOLS}))
    server.middleware.append(ReadOnlyScope())
    return server


def warn_if_unseeded() -> None:
    from mcp_server.db import transaction

    try:
        with transaction() as conn:
            docs = conn.execute("SELECT count(*) AS n FROM policy_docs").fetchone()["n"]
    except Exception as exc:
        log.warning("Could not check the database: %s", exc)
        return
    if docs == 0:
        log.warning("policy_docs is empty, so every refund will be refused. Run: python -m data.seed_orders --reset")


def warm_up_search() -> None:
    """Loads the embedding model and opens the Qdrant connection before the first request.

    Without this the first search pays for both, which can take longer than a caller is
    willing to wait, and a caller that retries on a fresh server only pays for them again.
    """
    name = os.getenv("QDRANT_COLLECTION") or policy.DEFAULT_COLLECTION
    try:
        # A model download prints its progress, and over stdio only the protocol may use stdout.
        with contextlib.redirect_stdout(sys.stderr):
            if not policy.get_client().collection_exists(name):
                log.warning("The %s collection is missing, so search_policy will fail. "
                            "Run: python -m data.index_policies", name)
                return
            policy.search_policy("where is my order", top_k=1)
    except Exception as exc:
        log.warning("Policy search is not ready: %s. Is Qdrant running? Try: docker compose up", str(exc)[:300])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Deflect support MCP server.")
    parser.add_argument("--transport", choices=["stdio", "http"], default=os.getenv("MCP_TRANSPORT") or "stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.getenv("MCP_HTTP_PORT") or 8931))
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)

    # stdout carries the protocol over stdio, so logs must only ever go to stderr.
    logging.basicConfig(level=args.log_level, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")
    server = build_server(args.log_level)
    warn_if_unseeded()

    full, read_only = os.getenv("MCP_HTTP_TOKEN"), os.getenv("MCP_HTTP_READONLY_TOKEN")
    if args.transport == "http" and not full:
        print("MCP_HTTP_TOKEN is not set. The HTTP transport refuses to start without a token.", file=sys.stderr)
        return 2
    warm_up_search()

    if args.transport == "stdio":
        server.run("stdio")
        return 0

    tokens = {full: "full"}
    if read_only and read_only != full:
        tokens[read_only] = "read"

    import uvicorn

    app = BearerGate(server.streamable_http_app(host=args.host), tokens)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
