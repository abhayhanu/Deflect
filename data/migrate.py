"""Applies the numbered SQL files in the migrations folder, once each, as the database owner.

schema.sql holds the tables. The migrations hold what came after, starting with Phase 3: the
agent's own database role, and the grants that make the audit log append only. That role may
insert into the audit log and read it. It has no UPDATE and no DELETE there, and a trigger
refuses both even for the owner. Only a full reseed, which truncates, empties the table.

The seed script runs this for you. Run this module by hand after pulling a new migration.
"""

import argparse
import sys
from pathlib import Path

from psycopg import sql

from data.db import APP_ROLE, app_password, connect, database_url

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def ensure_app_role(cur) -> None:
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,))
    verb = "ALTER" if cur.fetchone() else "CREATE"
    cur.execute(sql.SQL(verb + " ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(APP_ROLE), sql.Literal(app_password())))
    cur.execute("SELECT current_database()")
    database = cur.fetchone()[0]
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(APP_ROLE)))


def applied(cur) -> set[str]:
    cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
    cur.execute("SELECT name FROM schema_migrations")
    return {row[0] for row in cur.fetchall()}


def pending(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('schema_migrations') IS NOT NULL")
        if not cur.fetchone()[0]:
            return [p.name for p in migration_files()]
        cur.execute("SELECT name FROM schema_migrations")
        done = {row[0] for row in cur.fetchall()}
    return [p.name for p in migration_files() if p.name not in done]


def prepare_checkpoints() -> None:
    """The owner creates LangGraph's checkpoint tables, then hands the agent's role the rights
    it needs on them. The agent never has to create a table itself."""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection

    with Connection.connect(database_url(), autocommit=True, prepare_threshold=0) as conn:
        PostgresSaver(conn).setup()
        for table in CHECKPOINT_TABLES:
            conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
                sql.Identifier(table), sql.Identifier(APP_ROLE)))


def migrate(cur) -> list[str]:
    """Runs inside the caller's transaction. Returns the names it applied."""
    ensure_app_role(cur)
    done = applied(cur)
    ran = []
    for path in migration_files():
        if path.name in done:
            continue
        cur.execute(path.read_text(encoding="utf-8"))
        cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        ran.append(path.name)
    return ran


def main() -> int:
    argparse.ArgumentParser(description="Apply Deflect database migrations.").parse_args()
    from data.seed_orders import SCHEMA_FILE

    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        ran = migrate(cur)
    prepare_checkpoints()
    print(f"Applied {', '.join(ran)}." if ran else "Nothing to apply, the database is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
