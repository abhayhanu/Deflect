from typing import Annotated

from pydantic import Field

from mcp_server.db import fetch_all, fetch_one, transaction
from mcp_server.models import CustomerHistory, CustomerId, OrderId, OrderOut, ShipmentStatus

ORDER_SQL = """
SELECT order_id, customer_id, status, items, total_inr, refunded_inr, payment_method,
       shipping_city, placed_at, delivered_at, cancelled_at
FROM orders WHERE order_id = %s
"""

SHIPMENTS_SQL = """
SELECT direction, carrier, tracking_no, status, shipped_at, promised_by,
       last_event, last_location, last_event_at, delivered_at
FROM shipments WHERE order_id = %s ORDER BY direction
"""

REFUNDS_SQL = """
SELECT refund_id, order_id, amount_inr, reason_code, policy_doc_id, status, issued_at
FROM refunds WHERE order_id = %s ORDER BY issued_at
"""

HISTORY_SQL = """
SELECT order_id, status, total_inr, refunded_inr, placed_at, delivered_at
FROM orders WHERE customer_id = %s ORDER BY placed_at DESC LIMIT %s
"""

CUSTOMER_REFUNDS_SQL = """
SELECT r.refund_id, r.order_id, r.amount_inr, r.reason_code, r.status, r.issued_at
FROM refunds r JOIN orders o ON o.order_id = r.order_id
WHERE o.customer_id = %s ORDER BY r.issued_at DESC
"""


def load_order(conn, order_id: str) -> OrderOut | None:
    order = fetch_one(conn, ORDER_SQL, (order_id,))
    if order is None:
        return None
    order["shipments"] = fetch_all(conn, SHIPMENTS_SQL, (order_id,))
    order["refunds"] = fetch_all(conn, REFUNDS_SQL, (order_id,))
    return OrderOut(**order)


def get_order(order_id: OrderId) -> OrderOut | None:
    """Fetch one order with its items, amounts, payment method, shipments and refunds.

    Returns null when no order has this id. A missing order is not an error, it is usually a
    typo by the customer. The street address is never returned, only the delivery city.
    refunded_inr is the total already refunded, so total_inr minus refunded_inr is the most
    that can still be refunded. Read only.
    """
    with transaction() as conn:
        return load_order(conn, order_id)


def get_customer_history(
    customer_id: CustomerId,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
) -> CustomerHistory | None:
    """Recent orders for a customer, newest first, plus every refund the customer has had.

    Use the refunds to check rules that look across orders, such as one automatic lost in
    transit refund in 90 days. Returns null for an unknown customer. Read only.
    """
    with transaction() as conn:
        customer = fetch_one(conn, "SELECT city, created_at FROM customers WHERE customer_id = %s", (customer_id,))
        if customer is None:
            return None
        return CustomerHistory(
            customer_id=customer_id,
            city=customer["city"],
            customer_since=customer["created_at"],
            recent_orders=fetch_all(conn, HISTORY_SQL, (customer_id, limit)),
            refunds=fetch_all(conn, CUSTOMER_REFUNDS_SQL, (customer_id,)),
        )


def check_shipment(order_id: OrderId) -> ShipmentStatus | None:
    """Tracking for an order: carrier, tracking number, latest event, its location and time,
    the promised delivery date and the delivery scan.

    An order that has not shipped has no shipments yet. A returned order also has a return
    shipment. Returns null when no order has this id. Read only.
    """
    with transaction() as conn:
        order = fetch_one(conn, "SELECT order_id, status FROM orders WHERE order_id = %s", (order_id,))
        if order is None:
            return None
        return ShipmentStatus(order_id=order_id, order_status=order["status"],
                              shipments=fetch_all(conn, SHIPMENTS_SQL, (order_id,)))
