"""Queries the harness itself needs. Everything the agent reads about orders and customers now
goes through the MCP server, in the orders module of the mcp_server package.
"""

from datetime import datetime


def fetch_seed_anchor(conn) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM seed_meta WHERE key = 'anchor'")
        row = cur.fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def count_tool_requests(conn) -> int | None:
    """How many writes have gone through the MCP server since the last seed. None if the table
    does not exist yet, which means the database predates Phase 2."""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('tool_requests') IS NOT NULL")
        if not cur.fetchone()[0]:
            return None
        cur.execute("SELECT count(*) FROM tool_requests")
        return cur.fetchone()[0]
