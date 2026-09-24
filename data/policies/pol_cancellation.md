---
doc_id: pol_cancellation
title: Cancelling an Order
applies_to: [cancellation, refund_request, product_question]
version: 1
---

# Cancelling an Order

This policy covers requests to cancel an order before it arrives.

## Cancellation window

- An order can be cancelled free of charge while its status is **placed**, meaning it has not shipped.
- Once an order has shipped, it cannot be cancelled. The customer can refuse the delivery at the door. A refused order returns to our warehouse and is refunded in full within 7 days of the refusal.
- A delivered order cannot be cancelled. It can only be returned if it is eligible under `pol_return_window`.
- A cancelled order cannot be reinstated. The customer can place a new order.

## Refund on cancellation

- **Prepaid orders:** cancelling the order refunds the full amount automatically to the original payment method. No separate refund request is needed, and a second refund must never be issued for the same cancellation. Timelines are in `pol_refund_timelines`.
- **Cash on delivery orders:** nothing was paid, so there is nothing to refund.

## Partial cancellations and partial shipments

- Individual items cannot be removed from an order after it is placed. The customer can cancel the whole order and place a new one with the items they still want.
- Every order ships in a single package, so an order is never partly shipped. If a delivered package is missing one of the items, that is covered by `pol_not_as_described`.
