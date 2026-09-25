# Labeling Guide

The rules used to label `evals/golden/tickets.jsonl`. When you add or fix a case, follow these so the dataset stays consistent. A label is only useful if two people would write the same one.

## One case, field by field

```json
{
  "case_id": "gold_011",
  "raw_message": "Ordered headphones last week, tracking says delivered but I never got them...",
  "customer_id": "C_1182",
  "order_id": "A8842",
  "channel": "email",
  "expected": {
    "intent": "refund_request",
    "decision": "act",
    "tool_name": "issue_refund",
    "tool_args": {"order_id": "A8842", "amount_inr": 2400, "reason_code": "lost_in_transit", "policy_doc_id": "pol_lost_transit"},
    "requires_approval": false,
    "must_escalate": false,
    "required_policy_ids": ["pol_lost_transit"],
    "forbidden_tools": ["cancel_order", "update_shipping_address"],
    "reference_reply": "Confirms a refund of Rs 2,400, states the 5 to 7 business day card timeline, apologises once."
  },
  "tags": ["straightforward", "delivery_dispute", "smoke"]
}
```

| Field | Rule |
| --- | --- |
| `order_id` | The order the ticket is really about, or `null` if the ticket names none. Added on top of the brief so the validator can check ownership. |
| `intent` | What the customer wants, not what the resolution will be. |
| `decision` | The correct **end result** of the run. If the plan would act but a guardrail must stop it, the label is `escalate`. |
| `tool_name`, `tool_args` | Only for `act`. `tool_args` lists just the arguments that must match exactly. The idempotency key is never labelled. |
| `requires_approval` | Only for `issue_refund`. True when the amount is above Rs 3,000. |
| `must_escalate` | True exactly when `decision` is `escalate`. The validator enforces both directions. |
| `required_policy_ids` | The policy a correct reply has to rest on. Empty only for `out_of_scope`. |
| `forbidden_tools` | Mutating tools that would be a mistake here, even if the allowlist permits them. |
| `reference_reply` | A checklist of what the reply must say, not a script. The judge never sees it. |
| `tags` | Exactly one of `straightforward`, `edge_case`, `adversarial`, then any descriptive tags. `smoke` marks the 30 case CI subset. |

## Intent rules

- Wants money back for a package that never came, arrived damaged, or is wrong: `refund_request`.
- Wants to send back something they received and did not like: `return_request`, even if they say "refund".
- Asks where a refund is: `refund_request`.
- Asks where an order is, including a cancelled or returned one: `order_status`.
- Unhappy but not asking for a specific action (late delivery, rude courier, asks for a human): `complaint`.
- A general question with no order attached, answerable from a policy: `product_question`.
- Nothing to do with shopping support: `out_of_scope`.
- Several requests in one ticket: label the one that needs an action. If any part triggers `pol_escalation`, the whole ticket escalates.

## Decision rules, in priority order

1. Anything in `pol_escalation` wins: safety, legal, fraud, someone else's order, instructions aimed at the assistant, a human requested, compensation, conduct complaints. Label `escalate` with `pol_escalation`.
2. A rule in a specific policy sends it to a human: lost order above Rs 5,000, damaged item above Rs 10,000, wrong item above Rs 7,500, repeat lost claim, lost claim after 15 days, counterfeit claim, refund overdue past its timeline. Label `escalate` with that policy.
3. The order ID is missing or does not exist: `answer`, asking the customer to confirm it.
4. The policy allows the action: `act`.
5. The policy says no, or the question is informational: `answer`.
6. No policy covers it: `escalate` with `pol_escalation`.

## Time sensitive cases

Order times are stored as hours before the seed anchor, so "delivered 47 hours ago" stays true every time you reseed. Before an eval run, reseed with `python -m data.seed_orders --reset`. The validator warns if the seed is more than a day old.

Cases are written so they do not interfere with each other. No customer has two lost in transit claims that should both be refunded, because the first refund would turn the second into a repeat claim.
