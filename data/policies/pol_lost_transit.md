---
doc_id: pol_lost_transit
title: Lost in Transit and Delayed Shipments
applies_to: [order_status, refund_request, complaint]
version: 1
---

# Lost in Transit and Delayed Shipments

This policy covers three situations: a customer asking where an order is, a shipment that is late, and an order that tracking shows as delivered but the customer says never arrived. Items that arrived damaged are covered by `pol_damaged_goods`. Items that arrived but are wrong or missing from the package are covered by `pol_not_as_described`.

## Tracking and delivery estimates

- Orders are dispatched within 2 business days of being placed.
- Every order has a promised delivery date, shown at checkout and on the tracking page.
- When a customer asks about an order, share the current status, the latest tracking event, its location, and the promised delivery date.
- An order that has not shipped yet has no tracking events. Tell the customer it will be dispatched within 2 business days of the order date.

## Delayed shipments

- A shipment is delayed when it has not been delivered by its promised delivery date.
- Up to 7 days past the promised date: share the latest tracking event, apologise for the delay once, and confirm the order is still on its way. We do not offer compensation, vouchers or discounts for delays.
- More than 7 days past the promised date with no delivery: the shipment is declared lost and is handled under the refund rules for lost shipments below.

## Delivered but not received

- **Waiting period.** The delivery scan must be at least 48 hours old before a claim can be resolved. Before that, ask the customer to check with family, neighbours and building security or reception. Most of these packages turn up within 48 hours.
- **Claim window.** A claim must be raised within 15 days of the delivery scan. Claims raised after 15 days are not resolved automatically and go to the support team for review.

## Refund rules for lost shipments

- **Order value up to Rs 5,000 (inclusive):** issue a full refund of the order total minus anything already refunded, with reason code `lost_in_transit`. No carrier investigation is needed.
- **Order value above Rs 5,000:** a carrier investigation is required first. The support team handles it and replies within 5 business days. Do not issue a refund.
- **Repeat claims:** a customer can receive one automatic lost in transit refund in any 90 day period. If the customer already received a `lost_in_transit` refund in the last 90 days, the new claim goes to the support team.
- Refunds go back to the original payment method. Timelines are in `pol_refund_timelines`.
- We do not send free replacements for lost orders. The customer is welcome to place a new order.
