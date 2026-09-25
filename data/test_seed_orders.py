from datetime import datetime, timedelta, timezone

from data.fixtures import SCENARIOS
from data.seed_orders import MAX_ORDER_INR, MIN_ORDER_INR, build_dataset

ANCHOR = datetime(2026, 9, 24, 6, 30, tzinfo=timezone.utc)


def orders_by_id():
    return {o["order_id"]: o for o in build_dataset(ANCHOR)["orders"]}


def test_sizes_and_ranges():
    data = build_dataset(ANCHOR)
    orders = data["orders"]
    assert len(data["customers"]) == 60
    assert len(orders) == 200
    assert len({o["order_id"] for o in orders}) == 200
    assert all(MIN_ORDER_INR <= o["total_inr"] <= MAX_ORDER_INR for o in orders)
    assert all(o["refunded_inr"] <= o["total_inr"] for o in orders)


def test_build_is_deterministic():
    assert build_dataset(ANCHOR) == build_dataset(ANCHOR)


def test_named_edge_cases_exist():
    orders = orders_by_id()
    assert orders["A2486"]["refunded_inr"] == orders["A2486"]["total_inr"]
    assert 0 < orders["A2594"]["refunded_inr"] < orders["A2594"]["total_inr"]
    assert ANCHOR - orders["A9468"]["delivered_at"] == timedelta(hours=12)
    assert any(o["total_inr"] > 25_000 for o in orders.values())
    assert any(o["status"] == "cancelled" for o in orders.values())


def test_only_fixtures_carry_lost_in_transit_refunds():
    fixture_ids = {s.order_id for s in SCENARIOS}
    refunds = build_dataset(ANCHOR)["refunds"]
    lost = {r["order_id"] for r in refunds if r["reason_code"] == "lost_in_transit"}
    assert lost <= fixture_ids
