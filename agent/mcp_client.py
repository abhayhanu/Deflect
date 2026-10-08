"""The agent's only way to reach orders, policies and actions: the Deflect MCP server.

Graph nodes are plain synchronous functions, while an MCP session is async. So the session
lives on its own event loop in a background thread, and call() hands work to it and waits.

Every call has a 10 second timeout. A call that fails in transport, such as a timeout or a
dropped connection, is retried once on a fresh connection. A tool that answers with an error
is never retried: the server looked at the request and said no, and asking again is how a
customer gets refunded twice.
"""

import asyncio
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from mcp import Client, MCPError, StdioServerParameters
from mcp.types import CONNECTION_CLOSED, REQUEST_TIMEOUT

from agent.guardrails.policy import TOOL_TIMEOUT_S

log = logging.getLogger("agent.mcp_client")

REPO_ROOT = Path(__file__).resolve().parent.parent
CALL_TIMEOUT_S = TOOL_TIMEOUT_S
CONNECT_TIMEOUT_S = 60.0
DEFAULT_HTTP_PORT = 8931
# Only what the server needs. API keys for the chat model never reach it.
SERVER_ENV = ("POSTGRES_", "DATABASE_URL", "QDRANT_", "DEFLECT_EMBED_MODEL", "HF_", "FASTEMBED_")


class TransportError(RuntimeError):
    def __init__(self, message: str, generation: int = 0):
        super().__init__(message)
        self.generation = generation


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    read_only: bool


@dataclass
class ToolOutcome:
    result: Any = None
    error: str | None = None
    attempts: int = 1
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def parse_result(name: str, result) -> tuple[Any, str | None]:
    if result.is_error:
        text = " ".join(getattr(block, "text", "") for block in result.content).strip()
        return None, text.removeprefix(f"Error executing tool {name}: ") or "the tool failed without a message"

    data = result.structured_content
    if data is None:
        texts = [block.text for block in result.content if getattr(block, "text", None)]
        data = json.loads(texts[0]) if texts else None
    # The SDK wraps a return value that is not an object, such as a list or null, as {"result": value}.
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return data, None


class MCPClient:
    def __init__(self, session: Callable[[], Any], timeout: float = CALL_TIMEOUT_S, label: str = "mcp"):
        self.session = session
        self.timeout = timeout
        self.label = label
        self._lock = threading.Lock()
        self._generation = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: Client | None = None
        self._stop: asyncio.Event | None = None
        self._thread: threading.Thread | None = None
        self._specs: list[ToolSpec] | None = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self) -> None:
        with self._lock:
            if self._client is None:
                self._connect()

    def _connect(self) -> None:
        ready, failure = threading.Event(), []

        async def serve():
            self._loop, self._stop = asyncio.get_running_loop(), asyncio.Event()
            entered = None
            try:
                async with self.session() as client:
                    entered = self._client = client
                    ready.set()
                    await self._stop.wait()
            except BaseException as exc:
                failure.append(exc)
            finally:
                if self._client is entered:
                    self._client = None
                ready.set()

        self._thread = threading.Thread(target=asyncio.run, args=(serve(),), name=f"{self.label}-loop", daemon=True)
        self._thread.start()
        if not ready.wait(CONNECT_TIMEOUT_S) or self._client is None:
            reason = describe(failure[0]) if failure else "timed out"
            raise TransportError(f"could not connect to the MCP server: {reason}")
        self._generation += 1
        log.info("Connected to the MCP server over %s", self.label)

    def close(self) -> None:
        with self._lock:
            self._disconnect()

    def _disconnect(self) -> None:
        if self._loop and self._stop:
            try:
                self._loop.call_soon_threadsafe(self._stop.set)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=10)
        self._client = self._loop = self._stop = self._thread = None

    def _reconnect(self, generation: int) -> None:
        with self._lock:
            # Another thread may already have replaced the broken connection.
            if generation == self._generation and self._client is not None:
                self._disconnect()
            if self._client is None:
                self._connect()

    def _run(self, work: Callable[[Client], Any]):
        self.start()
        generation, client, loop = self._generation, self._client, self._loop

        async def bounded():
            return await asyncio.wait_for(work(client), self.timeout)

        try:
            return asyncio.run_coroutine_threadsafe(bounded(), loop).result(timeout=self.timeout + 5)
        except TimeoutError:
            raise TransportError(f"no answer within {self.timeout:g} s", generation) from None
        except MCPError as exc:
            if exc.error.code in (CONNECTION_CLOSED, REQUEST_TIMEOUT):
                raise TransportError(exc.error.message, generation) from exc
            raise
        except Exception as exc:
            raise TransportError(describe(exc), generation) from exc

    def call(self, name: str, args: dict) -> ToolOutcome:
        started = time.perf_counter()
        for attempt in (1, 2):
            try:
                raw = self._run(lambda c: c.call_tool(name, args, read_timeout_seconds=self.timeout))
            except TransportError as exc:
                if attempt == 2:
                    raise
                log.warning("Transport error on %s, retrying once on a new connection: %s", name, exc)
                self._reconnect(exc.generation)
                continue
            result, error = parse_result(name, raw)
            if error:
                log.info("Tool %s answered with an error: %s", name, error)
            return ToolOutcome(result, error, attempt, round((time.perf_counter() - started) * 1000))

    def tools(self) -> list[ToolSpec]:
        if self._specs is None:
            listing = self._run(lambda c: c.list_tools())
            self._specs = [
                ToolSpec(t.name, t.description or "", t.input_schema,
                         bool(t.annotations and t.annotations.read_only_hint))
                for t in listing.tools
            ]
        return self._specs


def describe(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def server_env(pin_clock: bool) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.startswith(SERVER_ENV)}
    if pin_clock:
        env["DEFLECT_CLOCK"] = "seed_anchor"
    return env


def stdio_session(pin_clock: bool = False):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server", "--transport", "stdio"],
        cwd=REPO_ROOT,
        env=server_env(pin_clock),
    )
    return lambda: Client(params)


def http_session(url: str, token: str | None):
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    @asynccontextmanager
    async def open_session():
        headers = {"Authorization": f"Bearer {token}"} if token else None
        async with create_mcp_http_client(headers=headers) as http:
            async with Client(streamable_http_client(url, http_client=http)) as client:
                yield client

    return open_session


def connect(pin_clock: bool = False) -> MCPClient:
    """stdio in development, HTTP in deployment, chosen by MCP_TRANSPORT.

    pin_clock asks a stdio server to use the seed anchor as now, like the agent does in evals.
    A server reached over HTTP was started by someone else and keeps its own clock.
    """
    if (os.getenv("MCP_TRANSPORT") or "stdio") == "http":
        port = os.getenv("MCP_HTTP_PORT") or DEFAULT_HTTP_PORT
        url = os.getenv("MCP_HTTP_URL") or f"http://127.0.0.1:{port}/mcp"
        if pin_clock:
            log.warning("The server at %s keeps its own clock, so time based rules may drift from the agent's", url)
        return MCPClient(http_session(url, os.getenv("MCP_HTTP_TOKEN")), label="http")
    return MCPClient(stdio_session(pin_clock), label="stdio")


@lru_cache
def shared_client() -> MCPClient:
    return connect()
