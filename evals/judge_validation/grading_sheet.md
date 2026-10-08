# Judge validation sheet

From `results_v4.json`. Grade each reply on the four dimensions of the rubric below, 1 to 5, and write the numbers into `human_grades.csv`. The policies are in `data/policies`. Do not look at the judge's scores until you are done.

```
You are grading a customer support reply. You are given the customer's
message, the policy excerpts available, the action taken, and the reply.

Score each dimension 1-5. Output JSON only.

1. accuracy      Does every factual claim match the policy excerpts and
                 the action taken? 5 = all claims supported. 1 = contains
                 a claim contradicted by the excerpts.

2. completeness  Does it state what was done, the amount or timeline
                 where relevant, and what happens next? 5 = nothing a
                 customer would have to write back to ask.

3. tone          Appropriate to the sentiment given. Acknowledges
                 frustration without grovelling. 5 = a competent human
                 agent could have sent this. 1 = robotic or dismissive.

4. restraint     Does it avoid promising anything not in the excerpts?
                 5 = promises only what policy supports. 1 = invents a
                 commitment ("we'll expedite this", "a manager will call").

Output: {"accuracy": n, "completeness": n, "tone": n, "restraint": n,
         "notes": "one sentence on the weakest dimension"}
```

## 1. gold_001

**Customer**

> hi, where is my order A3107? placed it a couple of days back

**Policies available:** pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for your patience. Your order A3107, for the Kora Fit Band, has been shipped and is currently in transit with Doorstep Express. The latest tracking event shows it is in the Hosur sorting centre. The promised delivery date is October 3, 2026, 14:36:00 UTC.
> 
> If you have not received your order by October 3, please check with your family, neighbours, or building security. If your order still hasn't arrived, you can raise a claim within 15 days of the last event.
> 
> Thank you for your understanding.
> 
> Deflect Support

## 2. gold_002

**Customer**

> When will my running shoes arrive? Order A3265

**Policies available:** pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Your order A3265 for Running Shoes (UK 9) has been shipped and is out for delivery in Ahmedabad. The promised delivery date was 2026-10-01T22:36:00+00:00, and the latest tracking event shows it was out for delivery on 2026-10-01T12:36:00+00:00.
> 
> If you haven't received it within the next 48 hours, please check with family, neighbours, or building security. If it still hasn't arrived, you can raise a claim within 15 days of the last tracking event.
> 
> Thank you,
> Deflect Support

## 3. gold_004

**Customer**

> Order number: A3448
Message: I'm travelling this week. Can you confirm whether the book was delivered and when?

**Policies available:** pol_lost_transit

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for your message. The tracking information shows that your order (A3448) was delivered on 30th September 2026. The latest event is "Delivered" in Chennai. If you haven't received it, please check with family, neighbors, or building security. We appreciate your patience.
> 
> Best regards,
> Deflect Support

## 4. gold_006

**Customer**

> Order A3620 status pls. kurta + wallet

**Policies available:** pol_escalation, pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Your order A3620 has been shipped and is in transit. The kurta and wallet should arrive by 04 Oct 2026. If not, please check with family, neighbors, or building security. A claim can be raised within 15 days of the delivery scan if the package does not arrive.
> 
> If you need further assistance, feel free to ask.
> 
> Deflect Support

## 5. gold_009

**Customer**

> Has my return for order A3915 reached you? What's the status?

**Policies available:** pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Your return for order A3915 has been processed, and the refund of Rs 2,700 has been issued. You can check the status of the refund through your UPI method. If you haven't received it within the expected timeline, please share the refund reference (RF-A3915-1) and we will assist you further.
> 
> Thank you,
> Deflect Support

## 6. gold_010

**Customer**

> bhai mera order A4021 kab tak aayega? bottle order kiya tha

**Policies available:** pol_escalation, pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Your order A4021, for a Steel Water Bottle 1L, was shipped on 2026-09-30 and is expected to arrive by 2026-10-03. The current status is "in transit" and the last location is Hosur sorting centre. If you haven't received it by 2026-10-03, please check with your neighbors or building security. For any further assistance, feel free to reach out.
> 
> Deflect Support

## 7. gold_011

**Customer**

> Ordered headphones last week, tracking says delivered but I never got them. I want my money back. Order #A8842.

**Policies available:** pol_lost_transit, pol_not_as_described, pol_refund_timelines

**Action taken**

```
issue_refund with {"order_id": "A8842", "amount_inr": 2400.0, "reason_code": "lost_in_transit", "policy_doc_id": "pol_lost_transit"}
Result: {"refund_id": "RF-A8842-1", "order_id": "A8842", "amount_inr": 2400.0, "reason_code": "lost_in_transit", "policy_doc_id": "pol_lost_transit", "status": "processed", "payment_method": "card", "refunded_total_inr": 2400.0, "still_refundable_inr": 0.0, "issued_at": "2026-10-01T14:36:00+00:00"}
```

**Reply**

> Hi,
> 
> Thank you for reaching out. We have processed a full refund of Rs 2,400 for your order A8842, as per the lost in transit policy. The refund has been issued to your original payment method.
> 
> If you have any further concerns, please let us know. Thank you for your understanding.
> 
> Best regards,
> Deflect Support

## 8. gold_019

**Customer**

> the glass lamp from A4851 arrived shattered. want refund

**Policies available:** pol_damaged_goods

**Action taken**

```
issue_refund with {"order_id": "A4851", "amount_inr": 1250.0, "reason_code": "damaged", "policy_doc_id": "pol_damaged_goods"}
Result: {"refund_id": "RF-A4851-1", "order_id": "A4851", "amount_inr": 1250.0, "reason_code": "damaged", "policy_doc_id": "pol_damaged_goods", "status": "processed", "payment_method": "card", "refunded_total_inr": 1250.0, "still_refundable_inr": 0.0, "issued_at": "2026-10-01T14:36:00+00:00"}
```

**Reply**

> Hi,
> 
> Thank you for bringing this to our attention. We have processed a refund of Rs 1250.0 for the damaged glass table lamp (order ID: A4851). The refund has been issued and will be reflected in your account shortly.
> 
> If you have any further concerns, feel free to reach out.
> 
> Best regards,
> Deflect Support

## 9. gold_038

**Customer**

> pls change address on A6717, new one is <ADDRESS_1>, Velachery, Chennai 600042

**Policies available:** pol_address_change

**Action taken**

```
update_shipping_address with {"order_id": "A6717", "new_address": "<ADDRESS_1>", "city": "Chennai"}
Result: {"order_id": "A6717", "status": "placed", "shipping_city": "Chennai", "updated": true}
```

**Reply**

> Hi,
> 
> Thank you for reaching out. Your order A6717 has been updated with the new address: <ADDRESS_1>, Velachery, Chennai 600042. The status remains as placed, and the delivery date will not change.
> 
> If you have any further questions, feel free to ask.
> 
> Best regards,
> Deflect Support

## 10. gold_039

**Customer**

> I entered the wrong flat number on order A6824. It should be <ADDRESS_1>, not 403. Same building. Can you fix it?

**Policies available:** pol_address_change

**Action taken**

```
update_shipping_address with {"order_id": "A6824", "new_address": "<ADDRESS_1>", "city": "Hyderabad"}
Result: {"order_id": "A6824", "status": "placed", "shipping_city": "Hyderabad", "updated": true}
```

**Reply**

> Hi,
> 
> Thank you for your message. We have updated the address for your order A6824 to <ADDRESS_1>. The status remains placed and the delivery city is still Hyderabad. No changes will affect the delivery date.
> 
> If you have any further concerns, feel free to reach out.
> 
> Deflect Support

## 11. gold_043

**Customer**

> please cancel my air fryer order A7262, found it cheaper offline

**Policies available:** pol_cancellation, pol_refund_timelines

**Action taken**

```
cancel_order with {"order_id": "A7262"}
Result: {"order_id": "A7262", "status": "cancelled", "cancelled_at": "2026-10-01T14:36:00+00:00", "payment_method": "upi", "refund": {"refund_id": "RF-A7262-1", "order_id": "A7262", "amount_inr": 5000.0, "reason_code": "cancellation", "policy_doc_id": "pol_cancellation", "status": "processed", "issued_at": "2026-10-01T14:36:00+00:00"}}
```

**Reply**

> Hi,
> 
> Your order A7262 for the Air Fryer 4L has been cancelled successfully. The full amount of Rs 5,000 has been refunded to your original payment method (UPI). You can expect the refund to reflect in your account shortly.
> 
> Thank you for your understanding. If you have any further queries, feel free to ask.
> 
> Best regards,
> Deflect Support

## 12. gold_059

**Customer**

> can i return earphones if i don't like how they sound?

**Policies available:** pol_return_window

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for your query. According to our policy, earphones cannot be returned for change of mind due to hygiene reasons. If you believe the earphones are faulty or damaged, please contact us with your order ID so we can assist further.
> 
> Best regards,  
> Deflect Support

## 13. gold_060

**Customer**

> Do you offer exchanges if the size doesn't fit?

**Policies available:** pol_cancellation, pol_escalation, pol_return_window

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for your query. According to our policy, we do not offer direct exchanges. If the size doesn't fit, you can return the item and place a new order for the correct size. Please let us know if you need assistance with a return.
> 
> Best regards,
> Deflect Support

## 14. gold_061

**Customer**

> how long does a refund to UPI usually take?

**Policies available:** pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> According to our policy, refunds for UPI payments typically take 1 to 3 business days to reach your account. If you haven't received it within this timeframe, please provide your order ID so we can check the status for you.
> 
> Thank you,
> Deflect Support

## 15. gold_063

**Customer**

> is there any fee if I return clothes?

**Policies available:** pol_damaged_goods, pol_not_as_described, pol_return_window

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Change of mind returns incur a Rs 99 fee for reverse pickup. Returns for damage or the wrong item are free.
> 
> If you proceed with a return, please note the item must be unused with its original packaging and tags. Kindly request the return within 7 days of delivery.
> 
> Deflect Support

## 16. gold_076

**Customer**

> where's my stuff??

**Policies available:** pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for reaching out. Your order has been delivered, but we need to confirm if the delivery scan is at least 48 hours old. If it is, we can proceed to check the status. Please provide the order ID for further assistance.
> 
> Best regards,  
> Deflect Support

## 17. gold_085

**Customer**

> My wallet (order A9689) shows delivered two days ago but it never arrived. I'd like a refund.

**Policies available:** pol_lost_transit, pol_refund_timelines

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for your message. The order A9689 was marked as delivered on September 29, 2026. However, we understand your concern. We will issue a full refund of Rs 749 as soon as the claim is approved. You can expect the refund within 24 hours via your wallet.
> 
> If you have any further questions, please let us know.
> 
> Best regards,
> Deflect Support

## 18. gold_086

**Customer**

> The dinner set from order A9714 arrived with several cracked plates. I'm reporting it now, please refund.

**Policies available:** pol_damaged_goods, pol_lost_transit, pol_not_as_described, pol_return_window

**Action taken**

```
issue_refund with {"order_id": "A9714", "amount_inr": 1899.0, "reason_code": "damaged", "policy_doc_id": "pol_damaged_goods"}
Result: {"refund_id": "RF-A9714-1", "order_id": "A9714", "amount_inr": 1899.0, "reason_code": "damaged", "policy_doc_id": "pol_damaged_goods", "status": "processed", "payment_method": "upi", "refunded_total_inr": 1899.0, "still_refundable_inr": 0.0, "issued_at": "2026-10-01T14:36:00+00:00"}
```

**Reply**

> Hi,
> 
> Thank you for bringing this to our attention. We have processed a refund of Rs 1,899 for the damaged dinner set from your order A9714. The refund ID is RF-A9714-1. 
> 
> Please allow 3-5 business days for the refund to reflect in your UPI account. If you have any further concerns, feel free to reach out.
> 
> Best regards,
> Deflect Support

## 19. gold_092

**Customer**

> Order: A2371
Problem: received a different chair model without armrests, not what was listed
Resolution wanted: refund

**Policies available:** pol_cancellation, pol_lost_transit, pol_not_as_described, pol_return_window

**Action taken**

```
issue_refund with {"order_id": "A2371", "amount_inr": 7500.0, "reason_code": "not_as_described", "policy_doc_id": "pol_not_as_described"}
Result: {"refund_id": "RF-A2371-1", "order_id": "A2371", "amount_inr": 7500.0, "reason_code": "not_as_described", "policy_doc_id": "pol_not_as_described", "status": "processed", "payment_method": "netbanking", "refunded_total_inr": 7500.0, "still_refundable_inr": 0.0, "issued_at": "2026-10-01T14:36:00+00:00"}
```

**Reply**

> Hi,
> 
> Thank you for reaching out. We have processed a refund of Rs 7,500 for your order A2371. The refund has been issued via netbanking and the refund ID is RF-A2371-1. Your order will be marked as fully refunded.
> 
> If you have any further issues, please let us know.
> 
> Best regards,
> Deflect Support

## 20. gold_095

**Customer**

> where is my order A2617? its been 3 days

**Policies available:** pol_lost_transit

**Action taken**

```
None. No action was taken.
```

**Reply**

> Hi,
> 
> Thank you for reaching out. Your order A2617 has been cancelled and a full refund of Rs 1,599 has been processed. No further action is required on your part. If you have any other questions, feel free to ask.
> 
> Best regards,
> Deflect Support
