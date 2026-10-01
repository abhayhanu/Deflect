import psycopg
import pytest

from data.db import connect
from data.queries import count_tool_requests, fetch_seed_anchor


@pytest.fixture(scope="module")
def conn():
    try:
        with connect(connect_timeout=2) as conn:
            if fetch_seed_anchor(conn) is None:
                pytest.skip("database is not seeded")
            yield conn
    except psycopg.OperationalError:
        pytest.skip("Postgres is not running")


def test_seed_anchor_is_timezone_aware(conn):
    assert fetch_seed_anchor(conn).tzinfo is not None


def test_tool_requests_can_be_counted(conn):
    assert count_tool_requests(conn) is not None
