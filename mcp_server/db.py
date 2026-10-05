"""Database access for the server. It reads the same settings as the rest of Deflect but
imports nothing from it, so the server can live in its own repository.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("POSTGRES_USER") or "deflect"
    password = quote(os.getenv("POSTGRES_PASSWORD") or "deflect", safe="")
    host = os.getenv("POSTGRES_HOST") or "localhost"
    port = os.getenv("POSTGRES_PORT") or "5434"
    name = os.getenv("POSTGRES_DB") or "deflect"
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"


@contextmanager
def transaction():
    """One connection, one transaction. It commits when the block ends and rolls back if it raises.

    A transaction opened inside this one becomes a savepoint, which is how a failed insert can be
    caught without losing the whole transaction.
    """
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5, autocommit=True) as conn:
        with conn.transaction():
            yield conn


def plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def fetch_all(conn, sql: str, params: tuple) -> list[dict]:
    rows = conn.execute(sql, params).fetchall()
    return [{k: plain(v) for k, v in row.items()} for row in rows]


def fetch_one(conn, sql: str, params: tuple) -> dict | None:
    rows = fetch_all(conn, sql, params)
    return rows[0] if rows else None


def now(conn) -> datetime:
    """The server's idea of now.

    Evals pin the agent's clock to the moment the database was seeded, and the process that
    starts the server can ask for the same pinned clock with DEFLECT_CLOCK=seed_anchor. A caller
    of a tool can never change it.
    """
    if os.getenv("DEFLECT_CLOCK") == "seed_anchor":
        row = conn.execute("SELECT value FROM seed_meta WHERE key = 'anchor'").fetchone()
        if row:
            return datetime.fromisoformat(row["value"])
    return datetime.now(timezone.utc)
