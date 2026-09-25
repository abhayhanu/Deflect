"""Builds the Deflect demo database: 60 customers, 200 orders, their shipments and refunds.

Every row is upserted, so running it again is safe. Use the reset option before an eval run
to start from a clean slate, and the dry run option to see the data without a database.
"""

import argparse
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

from data.catalog import CATALOG, PRODUCTS
from data.fixtures import CUSTOMERS, LOCALITIES, SCENARIOS, Refund, Scenario

TOTAL_ORDERS = 200
FILLER_SEED = 42
SEED_VERSION = "1"
MIN_ORDER_INR = 150
MAX_ORDER_INR = 45_000
RETURN_PICKUP_FEE_INR = 99

SCHEMA_FILE = Path(__file__).with_name("schema.sql")

CARRIERS = {"SwiftShip": "SS", "Parcelo": "PL", "Doorstep Express": "DX", "Lumen Logistics": "LL"}
SORTING_HUBS = ["Bhiwandi", "Nagpur", "Hyderabad", "Gurugram", "Hosur", "Kolkata", "Ahmedabad"]
BUILDINGS = [
    "Green Glen Apartments", "Sai Residency", "Lakeview Towers", "Shanti Niketan",
    "Palm Meadows", "Sunrise Enclave", "Orchid Heights", "Krishna Kunj",
]
POLICY_FOR_REASON = {
    "lost_in_transit": "pol_lost_transit",
    "damaged": "pol_damaged_goods",
    "not_as_described": "pol_not_as_described",
    "cancellation": "pol_cancellation",
    "return": "pol_return_window",
}
EVENT_TEXT = {
    "in_transit": "In transit",
    "out_for_delivery": "Out for delivery",
    "delivered": "Delivered",
}


def parse_anchor(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(second=0, microsecond=0)
    anchor = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=timezone.utc)
    return anchor.astimezone(timezone.utc)


def build_customers(anchor: datetime) -> tuple[list[dict], dict[str, str]]:
    customers, addresses = [], {}
    for customer_id, full_name, city in CUSTOMERS:
        rng = random.Random(customer_id)
        first, last = full_name.lower().split(" ", 1)
        locality, pin = rng.choice(LOCALITIES[city])
        flat = f"{rng.randint(1, 14)}{rng.randint(0, 1)}{rng.randint(1, 4)}"
        addresses[customer_id] = f"Flat {flat}, {rng.choice(BUILDINGS)}, {locality}, {city} {pin}"
        customers.append({
            "customer_id": customer_id,
            "full_name": full_name,
            "email": f"{first}.{last.replace(' ', '')}@example.com",
            "phone": f"+91 9{rng.randint(100_000_000, 999_999_999)}",
            "city": city,
            "created_at": anchor - timedelta(days=rng.randint(200, 900)),
        })
    return customers, addresses


def line_items(items: tuple) -> list[dict]:
    lines = []
    for entry in items:
        sku, variant = entry if isinstance(entry, tuple) else (entry, None)
        product = CATALOG[sku]
        line = {
            "sku": sku,
            "name": product.name,
            "category": product.category,
            "qty": 1,
            "unit_price_inr": product.price_inr,
        }
        if variant:
            line["variant"] = variant
        lines.append(line)
    return lines


def timeline(s: Scenario, anchor: datetime) -> dict:
    def ago(hours):
        return anchor - timedelta(hours=hours)

    t = dict.fromkeys(["placed_at", "shipped_at", "promised_by", "delivered_at", "cancelled_at"])

    if s.status == "placed":
        t["placed_at"] = ago(s.placed_h)
    elif s.status == "cancelled":
        t["placed_at"] = ago(s.placed_h)
        t["cancelled_at"] = ago(s.cancelled_h)
    elif s.status == "shipped":
        t["shipped_at"] = ago(s.shipped_h)
        t["placed_at"] = t["shipped_at"] - timedelta(hours=24)
        t["promised_by"] = anchor + timedelta(hours=s.promised_in_h)
    else:
        t["delivered_at"] = ago(s.delivered_h)
        if s.late_by_h:
            t["promised_by"] = t["delivered_at"] - timedelta(hours=s.late_by_h)
        else:
            t["promised_by"] = t["delivered_at"] + timedelta(hours=24)
        t["placed_at"] = t["promised_by"] - timedelta(hours=120)
        t["shipped_at"] = t["placed_at"] + timedelta(hours=24)
    return t


def forward_shipment(s: Scenario, t: dict, city: str, anchor: datetime, rng: random.Random) -> dict:
    carrier = rng.choice(list(CARRIERS))
    delivered = s.status in ("delivered", "returned")
    status = "delivered" if delivered else s.last_event

    if delivered:
        location, event_at = city, t["delivered_at"]
    elif status == "out_for_delivery":
        location, event_at = city, anchor - timedelta(hours=s.last_event_h or 2)
    else:
        location = f"{rng.choice(SORTING_HUBS)} sorting centre"
        event_at = anchor - timedelta(hours=s.last_event_h or min(s.shipped_h, 8))

    return {
        "shipment_id": f"SH-{s.order_id}-F",
        "order_id": s.order_id,
        "direction": "forward",
        "carrier": carrier,
        "tracking_no": f"{CARRIERS[carrier]}{rng.randint(10**9, 10**10 - 1)}",
        "status": status,
        "shipped_at": t["shipped_at"],
        "promised_by": t["promised_by"],
        "last_event": EVENT_TEXT[status],
        "last_location": location,
        "last_event_at": event_at,
        "delivered_at": t["delivered_at"],
    }


def return_shipment(s: Scenario, city: str, anchor: datetime, rng: random.Random) -> dict:
    picked_up = anchor - timedelta(hours=s.pickup_h)
    arrived = picked_up + timedelta(hours=24)
    at_warehouse = arrived <= anchor
    carrier = rng.choice(list(CARRIERS))
    return {
        "shipment_id": f"SH-{s.order_id}-R",
        "order_id": s.order_id,
        "direction": "return",
        "carrier": carrier,
        "tracking_no": f"{CARRIERS[carrier]}R{rng.randint(10**8, 10**9 - 1)}",
        "status": "delivered" if at_warehouse else "in_transit",
        "shipped_at": picked_up,
        "promised_by": arrived,
        "last_event": "Delivered to warehouse" if at_warehouse else "Picked up",
        "last_location": "Bhiwandi warehouse" if at_warehouse else city,
        "last_event_at": arrived if at_warehouse else picked_up,
        "delivered_at": arrived if at_warehouse else None,
    }


def order_rows(s: Scenario, anchor: datetime, customer_city: dict, addresses: dict):
    rng = random.Random(s.order_id)
    city = customer_city[s.customer_id]
    items = line_items(s.items)
    total = sum(line["unit_price_inr"] * line["qty"] for line in items)
    t = timeline(s, anchor)

    shipments = []
    if t["shipped_at"]:
        shipments.append(forward_shipment(s, t, city, anchor, rng))
    if s.status == "returned":
        shipments.append(return_shipment(s, city, anchor, rng))

    refunds, refunded = [], 0
    for n, r in enumerate(s.refunds, start=1):
        amount = r.amount_inr if r.amount_inr is not None else total - refunded
        refund_id = f"RF-{s.order_id}-{n}"
        refunds.append({
            "refund_id": refund_id,
            "order_id": s.order_id,
            "amount_inr": amount,
            "reason_code": r.reason,
            "policy_doc_id": POLICY_FOR_REASON.get(r.reason),
            "status": r.status,
            "idempotency_key": f"seed:{refund_id}",
            "issued_at": anchor - timedelta(hours=r.hours_ago),
        })
        if r.status != "failed":
            refunded += amount

    order = {
        "order_id": s.order_id,
        "customer_id": s.customer_id,
        "status": s.status,
        "items": items,
        "total_inr": total,
        "refunded_inr": refunded,
        "payment_method": s.payment,
        "shipping_address": addresses[s.customer_id],
        "shipping_city": city,
        "placed_at": t["placed_at"],
        "delivered_at": t["delivered_at"],
        "cancelled_at": t["cancelled_at"],
    }
    return order, shipments, refunds


def filler_scenarios(count: int) -> list[Scenario]:
    rng = random.Random(FILLER_SEED)
    taken = {s.order_id for s in SCENARIOS}
    customer_ids = [c[0] for c in CUSTOMERS]
    statuses = ["delivered", "returned", "cancelled", "shipped", "placed"]
    payments = ["upi", "card", "cod", "netbanking", "wallet"]

    fillers = []
    while len(fillers) < count:
        order_id = f"A{rng.randint(1000, 9999)}"
        if order_id in taken:
            continue

        skus = [p.sku for p in rng.sample(PRODUCTS, rng.choice([1, 1, 1, 2, 2, 3]))]
        total = sum(CATALOG[sku].price_inr for sku in skus)
        if not MIN_ORDER_INR <= total <= MAX_ORDER_INR:
            continue

        taken.add(order_id)
        status = rng.choices(statuses, weights=[55, 8, 10, 12, 15])[0]
        payment = rng.choices(payments, weights=[40, 25, 15, 10, 10])[0]
        base = dict(order_id=order_id, customer_id=rng.choice(customer_ids), items=tuple(skus), payment=payment, status=status)

        if status == "delivered":
            s = Scenario(**base, delivered_h=rng.randint(8 * 24, 180 * 24))
        elif status == "returned":
            delivered_h = rng.randint(20 * 24, 150 * 24)
            pickup_h = delivered_h - 72
            refund = Refund("return", pickup_h - 48, total - RETURN_PICKUP_FEE_INR)
            s = Scenario(**base, delivered_h=delivered_h, pickup_h=pickup_h, refunds=(refund,))
        elif status == "cancelled":
            placed_h = rng.randint(5 * 24, 150 * 24)
            cancelled_h = placed_h - rng.randint(1, 20)
            refunds = () if payment == "cod" else (Refund("cancellation", cancelled_h),)
            s = Scenario(**base, placed_h=placed_h, cancelled_h=cancelled_h, refunds=refunds)
        elif status == "shipped":
            s = Scenario(**base, shipped_h=rng.randint(6, 60), promised_in_h=rng.randint(12, 96))
        else:
            s = Scenario(**base, placed_h=rng.randint(1, 36))
        fillers.append(s)
    return fillers


def build_dataset(anchor: datetime) -> dict[str, list[dict]]:
    customers, addresses = build_customers(anchor)
    customer_city = {c["customer_id"]: c["city"] for c in customers}

    data = {"customers": customers, "orders": [], "shipments": [], "refunds": []}
    for s in SCENARIOS + filler_scenarios(TOTAL_ORDERS - len(SCENARIOS)):
        order, shipments, refunds = order_rows(s, anchor, customer_city, addresses)
        data["orders"].append(order)
        data["shipments"].extend(shipments)
        data["refunds"].extend(refunds)
    return data


def upsert(cur, table: str, key: str, rows: list[dict]) -> None:
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join(f"%({c})s" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != key)
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT ({key}) DO UPDATE SET {updates}"
    )
    cur.executemany(sql, rows)


def write_to_db(data: dict, anchor: datetime, reset: bool) -> None:
    from psycopg.types.json import Jsonb

    from data.db import connect

    orders = [{**o, "items": Jsonb(o["items"])} for o in data["orders"]]

    with connect() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        if reset:
            cur.execute(
                "TRUNCATE audit_log, approvals, refunds, shipments, orders, customers, seed_meta "
                "RESTART IDENTITY CASCADE"
            )
        upsert(cur, "customers", "customer_id", data["customers"])
        upsert(cur, "orders", "order_id", orders)
        upsert(cur, "shipments", "shipment_id", data["shipments"])
        upsert(cur, "refunds", "refund_id", data["refunds"])

        # Runtime refunds from later phases are kept, so the stored total is recomputed from the table.
        cur.execute(
            """
            UPDATE orders o SET refunded_inr = r.total
            FROM (SELECT order_id, SUM(amount_inr) AS total FROM refunds
                  WHERE status <> 'failed' GROUP BY order_id) r
            WHERE r.order_id = o.order_id
            """
        )
        meta = [
            {"key": "anchor", "value": anchor.isoformat()},
            {"key": "seed_version", "value": SEED_VERSION},
            {"key": "order_count", "value": str(len(orders))},
        ]
        upsert(cur, "seed_meta", "key", meta)


def print_summary(data: dict, anchor: datetime) -> None:
    orders = data["orders"]
    statuses = Counter(o["status"] for o in orders)
    totals = [o["total_inr"] for o in orders]
    per_customer = Counter(o["customer_id"] for o in orders)
    refunded_customers = {o["customer_id"] for o in orders if o["refunded_inr"] > 0}

    print(f"Anchor time      {anchor.isoformat()}")
    print(f"Customers        {len(data['customers'])}  ({sum(1 for n in per_customer.values() if n > 1)} with more than one order)")
    print(f"Orders           {len(orders)}  (Rs {min(totals):,} to Rs {max(totals):,})")
    print(f"By status        {', '.join(f'{k} {v}' for k, v in statuses.most_common())}")
    print(f"Shipments        {len(data['shipments'])}")
    print(f"Refunds          {len(data['refunds'])}  across {len(refunded_customers)} customers")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Deflect database.")
    parser.add_argument("--reset", action="store_true", help="truncate every table before seeding")
    parser.add_argument("--anchor", help="ISO time that all order times are relative to, default is now")
    parser.add_argument("--dry-run", action="store_true", help="build and summarise without touching the database")
    args = parser.parse_args()

    anchor = parse_anchor(args.anchor or os.getenv("DEFLECT_SEED_ANCHOR"))
    data = build_dataset(anchor)

    if not args.dry_run:
        try:
            write_to_db(data, anchor, reset=args.reset)
        except psycopg.OperationalError as exc:
            print("Could not connect to Postgres. Is it running? Try: docker compose up -d", file=sys.stderr)
            print(f"Details: {exc}", file=sys.stderr)
            return 1

    print_summary(data, anchor)
    print("Dry run, nothing written." if args.dry_run else "Seed complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
