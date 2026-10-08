"""Fixtures for the server tests.

The tests write refunds and cancel orders, so they run against their own database, the normal
name with _test on the end, seeded fresh from the Deflect demo data. Your dev database is
never touched.
"""

import asyncio
import hashlib
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import psycopg
import pytest
from mcp import Client
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from mcp_server.db import database_url
from mcp_server.server import build_server
from mcp_server.tools import policy

SERVER = build_server()
ANCHOR = datetime.now(timezone.utc).replace(second=0, microsecond=0)


class HashEmbedder:
    """A tiny bag of words embedder so the tests need no model download."""

    dim = 256

    def embed_query(self, text):
        vector = [0.0] * self.dim
        for word in re.findall(r"[a-z]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]


@dataclass
class Reply:
    data: object
    error: str | None


def call(name: str, args: dict) -> Reply:
    """Calls a tool over MCP, in process, so argument validation runs exactly as it would for a client."""
    async def go():
        async with Client(SERVER) as client:
            return await client.call_tool(name, args)

    result = asyncio.run(go())
    if result.is_error:
        return Reply(None, result.content[0].text.removeprefix(f"Error executing tool {name}: "))
    data = result.structured_content
    if isinstance(data, dict) and set(data) == {"result"}:
        data = data["result"]
    return Reply(data, None)


def isolated_database_url() -> str:
    params = conninfo_to_dict(database_url())
    params["dbname"] = f"{params.get('dbname') or 'deflect'}_test"
    return make_conninfo(**params)


def reseed() -> None:
    from data.seed_orders import build_dataset, write_to_db

    write_to_db(build_dataset(ANCHOR), ANCHOR, reset=True)


@pytest.fixture(scope="session", autouse=True)
def test_db():
    url = isolated_database_url()
    name = conninfo_to_dict(url)["dbname"]
    try:
        with psycopg.connect(database_url(), autocommit=True, connect_timeout=2) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running")

    previous = {k: os.environ.get(k) for k in ("DATABASE_URL", "DEFLECT_CLOCK")}
    os.environ["DATABASE_URL"] = url
    os.environ["DEFLECT_CLOCK"] = "seed_anchor"
    reseed()
    yield url
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture
def fresh_db(test_db):
    reseed()
    return test_db


@pytest.fixture(scope="session")
def policy_index():
    from qdrant_client import QdrantClient

    from data.index_policies import ensure_indexed

    client, embedder = QdrantClient(location=":memory:"), HashEmbedder()
    ensure_indexed(client, embedder)
    return client, embedder


@pytest.fixture
def indexed(policy_index, monkeypatch):
    client, embedder = policy_index
    monkeypatch.setattr(policy, "get_client", lambda: client)
    monkeypatch.setattr(policy, "get_embedder", lambda: embedder)
    return policy_index


def query(sql: str, params: tuple = ()) -> list[dict]:
    from psycopg.rows import dict_row

    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as conn:
        return conn.execute(sql, params).fetchall()
