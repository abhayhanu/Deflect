import psycopg
import pytest
from psycopg.errors import InsufficientPrivilege

from data import testdb
from data.db import app_database_url

ROW = ("INSERT INTO audit_log (ticket_id, trace_id, tool_name, tool_args, authorized_by, latency_ms) "
       "VALUES ('t1', 't1', 'get_order', '{}', 'policy', 1) RETURNING id")


@pytest.fixture
def owner_url(monkeypatch):
    url = testdb.prepare(monkeypatch)
    if url is None:
        pytest.skip("Postgres is not running")
    return url


def test_the_app_role_can_add_audit_rows_but_never_change_or_remove_them(owner_url):
    with psycopg.connect(app_database_url(), autocommit=True) as app:
        row_id = app.execute(ROW).fetchone()[0]
        assert app.execute("SELECT count(*) FROM audit_log WHERE id = %s", (row_id,)).fetchone()[0] == 1
        for statement in ("UPDATE audit_log SET error = 'rewritten' WHERE id = %s",
                          "DELETE FROM audit_log WHERE id = %s"):
            with pytest.raises(InsufficientPrivilege):
                app.execute(statement, (row_id,))
        with pytest.raises(InsufficientPrivilege):
            app.execute("TRUNCATE audit_log")


def test_even_the_owner_cannot_rewrite_an_audit_row(owner_url):
    with psycopg.connect(owner_url, autocommit=True) as owner:
        row_id = owner.execute(ROW).fetchone()[0]
        for statement in ("UPDATE audit_log SET error = 'rewritten' WHERE id = %s", "DELETE FROM audit_log WHERE id = %s"):
            with pytest.raises(InsufficientPrivilege, match="append only"):
                owner.execute(statement, (row_id,))


def test_the_app_role_can_decide_approvals_but_not_touch_shop_data(owner_url):
    with psycopg.connect(app_database_url(), autocommit=True) as app:
        app.execute("INSERT INTO approvals (approval_id, ticket_id, thread_id, tool_name, tool_args) "
                    "VALUES ('apr_grant_test', 't1', 't1', 'issue_refund', '{}') ON CONFLICT DO NOTHING")
        app.execute("UPDATE approvals SET status = 'approved' WHERE approval_id = 'apr_grant_test'")
        for statement in ("SELECT count(*) FROM orders", "UPDATE refunds SET amount_inr = 1", "DELETE FROM approvals"):
            with pytest.raises(InsufficientPrivilege):
                app.execute(statement)


def test_migrating_twice_changes_nothing(owner_url, monkeypatch):
    from data.migrate import pending

    assert testdb.prepare(monkeypatch) == owner_url
    with psycopg.connect(owner_url) as conn:
        assert pending(conn) == []
