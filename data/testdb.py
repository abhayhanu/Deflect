"""A separate database for tests that write, your database name with _test on the end.

It is created if missing, then migrated like the real one, so the agent's role and its grants
exist there too. Tests that need it skip when Postgres is not running.
"""

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from data.db import database_url


def isolated_url() -> str:
    params = conninfo_to_dict(database_url())
    name = params.get("dbname") or "deflect"
    params["dbname"] = name if name.endswith("_test") else f"{name}_test"
    return make_conninfo(**params)


def prepare(monkeypatch) -> str | None:
    """Points DATABASE_URL at the test database and migrates it. Returns None without Postgres."""
    url = isolated_url()
    name = conninfo_to_dict(url)["dbname"]
    try:
        with psycopg.connect(database_url(), autocommit=True, connect_timeout=2) as conn:
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                conn.execute(f'CREATE DATABASE "{name}"')
    except psycopg.OperationalError:
        return None

    monkeypatch.setenv("DATABASE_URL", url)
    from data.migrate import migrate, prepare_checkpoints
    from data.seed_orders import SCHEMA_FILE

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        migrate(cur)
    prepare_checkpoints()
    return url
