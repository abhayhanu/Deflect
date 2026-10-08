"""Attacks the server the way a hostile or confused client would. Passing tests are better
evidence than a document, so every claim in SECURITY.md has a test here."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from mcp import Client

from mcp_server.tests.conftest import SERVER, call, query

INJECTION = "A1234' OR '1'='1'; DROP TABLE orders; --"
TRAVERSAL = ["../../etc/passwd", "..\\..\\windows\\win.ini", "/etc/passwd", "data/policies/pol_lost_transit.md",
             "pol_lost_transit/../../.env", "file:///etc/passwd"]

VALID_ARGS = {
    "get_order": {"order_id": "A8842"},
    "get_customer_history": {"customer_id": "C_1182"},
    "search_policy": {"query": "where is my parcel", "intent": "order_status"},
    "check_shipment": {"order_id": "A3107"},
    "update_shipping_address": {"order_id": "A6472", "new_address": "Flat 9, Palm Grove, Koramangala 560095",
                                "city": "Bengaluru", "idempotency_key": "sec:address:0001"},
    "create_return_label": {"order_id": "A5401", "idempotency_key": "sec:return:0001"},
    "cancel_order": {"order_id": "A7262", "idempotency_key": "sec:cancel:0001"},
    "issue_refund": {"order_id": "A8842", "amount_inr": 100, "reason_code": "damaged",
                     "policy_doc_id": "pol_damaged_goods", "idempotency_key": "sec:refund:0001"},
    "escalate_to_human": {"ticket_id": "sec_1", "reason": "legal", "summary": "Customer mentions a legal notice.",
                          "idempotency_key": "sec:escalate:0001"},
}


def schemas() -> dict[str, dict]:
    async def go():
        async with Client(SERVER) as client:
            return {t.name: t.input_schema for t in (await client.list_tools()).tools}
    return asyncio.run(go())


def string_fields(schema: dict) -> list[str]:
    def is_string(prop):
        return prop.get("type") == "string" or any(o.get("type") == "string" for o in prop.get("anyOf", []))
    return [name for name, prop in schema["properties"].items() if is_string(prop)]


def table_counts() -> dict:
    return {t: query(f"SELECT count(*) AS n FROM {t}")[0]["n"] for t in ("customers", "orders", "shipments", "policy_docs")}


def test_exactly_nine_tools_and_none_touch_files_config_or_logs():
    found = schemas()
    assert set(found) == set(VALID_ARGS)
    words = ("path", "file", "dir", "url", "env", "config", "log")
    for name, schema in found.items():
        assert not [p for p in schema["properties"] if any(w in p for w in words)], name


def test_every_argument_is_bounded():
    for name, schema in schemas().items():
        for field, prop in schema["properties"].items():
            branch = next((o for o in prop.get("anyOf", []) if o.get("type") != "null"), prop)
            if branch.get("type") == "string":
                assert "pattern" in branch or "enum" in branch or "maxLength" in branch, f"{name}.{field}"
            if branch.get("type") in ("number", "integer"):
                assert {"minimum", "exclusiveMinimum"} & set(branch), f"{name}.{field}"


@pytest.mark.parametrize("attempt", TRAVERSAL)
def test_path_traversal_is_refused(fresh_db, attempt):
    assert call("get_order", {"order_id": attempt}).error
    assert call("check_shipment", {"order_id": attempt}).error
    refund = {**VALID_ARGS["issue_refund"], "policy_doc_id": attempt}
    assert call("issue_refund", refund).error
    assert query("SELECT count(*) AS n FROM refunds WHERE idempotency_key = 'sec:refund:0001'")[0]["n"] == 0


def test_a_well_formed_but_unknown_policy_id_is_still_refused(fresh_db):
    reply = call("issue_refund", {**VALID_ARGS["issue_refund"], "policy_doc_id": "pol_passwd"})
    assert reply.error.startswith("ungrounded")


def test_search_only_ever_returns_policy_sections(indexed):
    hits = call("search_policy", {"query": "../../etc/passwd .env DATABASE_URL"}).data
    known = {r["doc_id"] for r in query("SELECT doc_id FROM policy_docs")}
    assert hits and {h["doc_id"] for h in hits} <= known


def test_sql_injection_in_every_string_field(fresh_db, indexed):
    before = table_counts()
    tried = 0
    for name, schema in schemas().items():
        for field in string_fields(schema):
            call(name, {**VALID_ARGS[name], field: INJECTION})
            tried += 1
    assert tried >= 15
    assert table_counts() == before
    assert query("SELECT count(*) AS n FROM tool_requests WHERE idempotency_key = %s", (INJECTION,))[0]["n"] == 0


def test_free_text_is_stored_as_data_never_run(fresh_db):
    assert call("update_shipping_address", {**VALID_ARGS["update_shipping_address"], "new_address": INJECTION}).error is None
    stored = query("SELECT shipping_address FROM orders WHERE order_id = 'A6472'")[0]["shipping_address"]
    assert stored == INJECTION
    assert table_counts()["orders"] == 200


def test_duplicate_idempotency_key_is_refused(fresh_db):
    first = call("cancel_order", VALID_ARGS["cancel_order"])
    second = call("cancel_order", VALID_ARGS["cancel_order"])
    assert first.error is None
    assert second.error.startswith("duplicate_request")
    assert query("SELECT count(*) AS n FROM refunds WHERE order_id = 'A7262'")[0]["n"] == 1


def test_the_database_itself_enforces_the_key(fresh_db):
    import os

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("INSERT INTO tool_requests (idempotency_key, tool_name) VALUES ('raw:key:0001', 'issue_refund')")
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO tool_requests (idempotency_key, tool_name) VALUES ('raw:key:0001', 'issue_refund')")


def test_simultaneous_duplicates_refund_once(fresh_db):
    with ThreadPoolExecutor(max_workers=6) as pool:
        replies = list(pool.map(lambda _: call("issue_refund", VALID_ARGS["issue_refund"]), range(6)))
    assert sum(r.error is None for r in replies) == 1
    assert all(r.error.startswith("duplicate_request") for r in replies if r.error)
    assert query("SELECT count(*) AS n FROM refunds WHERE order_id = 'A8842'")[0]["n"] == 1


def test_an_argument_the_tool_does_not_declare_is_refused(fresh_db):
    before = table_counts()
    for name, args in VALID_ARGS.items():
        reply = call(name, {**args, "reason_code": "goodwill", "approved_by": "admin"})
        assert reply.error and reply.error.startswith("unknown_argument:"), name
        assert "approved_by" in reply.error and "reason_code" in reply.error or name == "issue_refund"
    assert query("SELECT count(*) AS n FROM tool_requests")[0]["n"] == 0
    assert table_counts() == before


def test_every_published_schema_forbids_extra_arguments():
    assert all(schema.get("additionalProperties") is False for schema in schemas().values())
