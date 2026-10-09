import time

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from agent.mcp_client import MCPClient, TransportError


def make_server(slow_calls: int = 0):
    server = MCPServer("test")
    seen = {"refund": 0, "slow": 0}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    def lookup(order_id: str) -> dict | None:
        """Read only."""
        return None if order_id == "A0000" else {"order_id": order_id}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
    def refund(order_id: str) -> dict:
        """Mutating."""
        seen["refund"] += 1
        raise ToolError("exceeds_refundable: nothing left to refund")

    @server.tool()
    def slow(order_id: str) -> dict:
        seen["slow"] += 1
        if seen["slow"] <= slow_calls:
            time.sleep(1.5)
        return {"order_id": order_id}

    return server, seen


def test_results_are_unwrapped_and_null_is_kept():
    server, _ = make_server()
    with MCPClient(lambda: Client(server)) as tools:
        assert tools.call("lookup", {"order_id": "A1"}).result == {"order_id": "A1"}
        outcome = tools.call("lookup", {"order_id": "A0000"})
        assert outcome.ok and outcome.result is None


def test_a_business_error_is_returned_and_never_retried():
    server, seen = make_server()
    with MCPClient(lambda: Client(server)) as tools:
        outcome = tools.call("refund", {"order_id": "A1"})
    assert outcome.error == "exceeds_refundable: nothing left to refund"
    assert outcome.attempts == 1 and seen["refund"] == 1


def test_a_timeout_is_retried_once_on_a_new_connection():
    server, seen = make_server(slow_calls=1)
    with MCPClient(lambda: Client(server), timeout=0.5) as tools:
        outcome = tools.call("slow", {"order_id": "A1"})
    assert outcome.ok and outcome.attempts == 2 and seen["slow"] == 2


def test_two_transport_failures_give_up():
    server, seen = make_server(slow_calls=5)
    with MCPClient(lambda: Client(server), timeout=0.5) as tools:
        with pytest.raises(TransportError):
            tools.call("slow", {"order_id": "A1"})
    assert seen["slow"] == 2


def test_tool_specs_carry_the_read_only_flag():
    server, _ = make_server()
    with MCPClient(lambda: Client(server)) as tools:
        specs = {s.name: s.read_only for s in tools.tools()}
    assert specs == {"lookup": True, "refund": False, "slow": False}


def test_an_unreachable_server_is_a_transport_error():
    from mcp import StdioServerParameters

    missing = StdioServerParameters(command="definitely-not-a-real-command-xyz")
    with pytest.raises(TransportError):
        MCPClient(lambda: Client(missing)).start()
