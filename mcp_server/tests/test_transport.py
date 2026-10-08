"""Runs the real server process on both transports."""

import asyncio
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

from mcp_server.server import READ_TOOLS, BearerGate, build_server

REPO_ROOT = Path(__file__).resolve().parents[2]
FULL, READ_ONLY = "test-full-token", "test-read-token"


def test_stdio_server_starts_and_answers(test_db):
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server", "--transport", "stdio"],
                                   cwd=REPO_ROOT, env={"DATABASE_URL": test_db})

    async def go():
        async with Client(params) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            order = await client.call_tool("get_order", {"order_id": "A8842"})
            return names, order.structured_content["result"]["order_id"]

    names, order_id = asyncio.run(go())
    assert len(names) == 9 and order_id == "A8842"


def test_http_refuses_to_start_without_a_token(monkeypatch):
    from mcp_server.server import main

    monkeypatch.delenv("MCP_HTTP_TOKEN", raising=False)
    assert main(["--transport", "http"]) == 2


@pytest.fixture(scope="module")
def http_url():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = BearerGate(build_server().streamable_http_app(), {FULL: "full", READ_ONLY: "read"})
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def over_http(url: str, token: str | None):
    async def go():
        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with create_mcp_http_client(headers=headers) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                names = {t.name for t in (await client.list_tools()).tools}
                write = await client.call_tool("cancel_order", {"order_id": "A0000", "idempotency_key": "http:test:0001"})
                read = await client.call_tool("get_order", {"order_id": "A8842"})
                return names, write.content[0].text, read.is_error
    return asyncio.run(go())


def test_full_token_sees_every_tool(http_url):
    names, write, read_failed = over_http(http_url, FULL)
    assert len(names) == 9 and not read_failed
    assert "not_found" in write


def test_read_only_token_sees_and_calls_only_read_tools(http_url):
    names, write, read_failed = over_http(http_url, READ_ONLY)
    assert names == READ_TOOLS and not read_failed
    assert write.startswith("forbidden")


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_no_valid_token_no_entry(http_url, token):
    with pytest.raises(BaseException):
        over_http(http_url, token)


def test_gate_answers_401_before_any_mcp_code_runs():
    from starlette.testclient import TestClient

    reached = []

    async def app(scope, receive, send):
        reached.append(scope["state"]["access"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    client = TestClient(BearerGate(app, {FULL: "full", READ_ONLY: "read"}))
    assert client.post("/mcp").status_code == 401
    assert client.post("/mcp", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/mcp", headers={"Authorization": f"Bearer {READ_ONLY}"}).status_code == 200
    assert reached == ["read"]


def test_warm_up_runs_one_search_and_never_stops_the_server(indexed, monkeypatch, caplog):
    from mcp_server import server
    from mcp_server.tools import policy

    searched = []
    monkeypatch.setattr(policy, "search_policy", lambda query, top_k=5: searched.append(query))
    server.warm_up_search()
    assert len(searched) == 1

    # A missing collection and a Qdrant that is down are both a warning, never a crash.
    monkeypatch.setenv("QDRANT_COLLECTION", "not_indexed_yet")
    server.warm_up_search()
    assert len(searched) == 1 and "python -m data.index_policies" in caplog.text

    def down():
        raise ConnectionError("connection refused")

    monkeypatch.setattr(policy, "get_client", down)
    server.warm_up_search()
    assert "Is Qdrant running" in caplog.text
