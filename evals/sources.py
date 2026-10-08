"""The order each ticket was about, as the agent saw it, for tools that read a finished run.

The reply checker and the judge both need the order record to tell a true statement from an
invented one. A results file now keeps it per ticket. A file from v6 or earlier does not, so it is
read back here: the shop data is seeded again at the file's anchor, which gives the same
orders, and each one is read through the MCP server the way the run read it.
"""

from datetime import datetime

from agent.mcp_client import MCPClient, TransportError
from agent.mcp_client import connect as connect_tools
from agent.nodes.retrieve import order_facts
from data.db import connect
from data.queries import count_tool_requests, fetch_seed_anchor


class NotReady(RuntimeError):
    """The orders cannot be read back as they were. The message says what to do."""


def read_orders(tools: MCPClient, records: list[dict], customers: dict[str, str], anchor: datetime) -> dict[str, dict | None]:
    orders = {}
    for record in records:
        order_id = record["predicted"].get("order_id")
        outcome = tools.call("get_order", {"order_id": order_id}) if order_id else None
        found = outcome.result if outcome and not outcome.error else None
        orders[record["case_id"]] = order_facts(found, customers.get(record["case_id"]), anchor) if found else None
    return orders


def attach_orders(results: dict, reseed_first: bool = False) -> int:
    """Puts the order under every case that lacks one. Returns how many were read back."""
    from evals.run import load_cases, reseed

    missing = [r for r in results["cases"] if "order" not in r]
    if not missing:
        return 0
    anchor = datetime.fromisoformat(results["meta"]["seed_anchor"])
    if reseed_first:
        reseed(anchor.isoformat())
    with connect() as conn:
        seeded, writes = fetch_seed_anchor(conn), count_tool_requests(conn)
    if seeded != anchor or writes:
        raise NotReady(f"This results file was recorded before orders were kept in it. To read them back the database "
                       f"must be seeded at {anchor.isoformat()} with nothing acted on yet. Run again with --reseed.")
    customers = {c.case_id: c.customer_id for c in load_cases("full")}
    try:
        tools = connect_tools(pin_clock=True)
        tools.start()
    except TransportError as exc:
        raise NotReady(f"Could not start the MCP server: {exc}") from exc
    with tools:
        orders = read_orders(tools, missing, customers, anchor)
    for record in missing:
        record["order"] = orders[record["case_id"]]
    return len(missing)
