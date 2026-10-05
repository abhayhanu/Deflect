"""Argument types and result shapes for every tool.

Each argument is bounded: ids must match their pattern, numbers have limits and choices are
closed lists. Anything that fails is rejected before a tool body runs.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

OrderId = Annotated[str, Field(pattern=r"^A\d{4,6}$", description="Order id, for example A8842")]
CustomerId = Annotated[str, Field(pattern=r"^C_\d{4}$", description="Customer id, for example C_1182")]
PolicyDocId = Annotated[str, Field(pattern=r"^pol_[a-z_]{2,40}$", description="A policy doc_id, for example pol_lost_transit")]
TicketId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$", description="The support ticket id")]
IdempotencyKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{8,128}$",
                                      description="Unique per intended action. Reusing a key is rejected.")]

Intent = Literal[
    "order_status", "refund_request", "return_request",
    "address_change", "cancellation", "complaint",
    "product_question", "out_of_scope",
]
RefundReason = Literal["lost_in_transit", "damaged", "not_as_described", "late_delivery", "goodwill"]
ReturnReason = Literal["change_of_mind", "damaged", "not_as_described"]
EscalationReason = Literal[
    "safety", "legal", "fraud_or_account", "someone_elses_order", "instructions_to_assistant",
    "human_requested", "compensation", "conduct", "payment_dispute", "privacy",
    "not_covered", "needs_review",
]


class Shipment(BaseModel):
    direction: Literal["forward", "return"]
    carrier: str
    tracking_no: str
    status: str
    shipped_at: str | None
    promised_by: str | None
    last_event: str | None
    last_location: str | None
    last_event_at: str | None
    delivered_at: str | None


class RefundRecord(BaseModel):
    refund_id: str
    order_id: str
    amount_inr: float
    reason_code: str
    policy_doc_id: str | None
    status: str
    issued_at: str


class OrderOut(BaseModel):
    order_id: str
    customer_id: str
    status: Literal["placed", "shipped", "delivered", "cancelled", "returned"]
    items: list[dict]
    total_inr: float
    refunded_inr: float
    payment_method: str
    shipping_city: str
    placed_at: str
    delivered_at: str | None
    cancelled_at: str | None
    shipments: list[Shipment]
    refunds: list[RefundRecord]


class OrderSummary(BaseModel):
    order_id: str
    status: str
    total_inr: float
    refunded_inr: float
    placed_at: str
    delivered_at: str | None


class CustomerRefund(BaseModel):
    refund_id: str
    order_id: str
    amount_inr: float
    reason_code: str
    status: str
    issued_at: str


class CustomerHistory(BaseModel):
    customer_id: str
    city: str
    customer_since: str
    recent_orders: list[OrderSummary]
    refunds: list[CustomerRefund]


class ShipmentStatus(BaseModel):
    order_id: str
    order_status: str
    shipments: list[Shipment]


class PolicyHit(BaseModel):
    doc_id: str
    title: str
    chunk: str
    score: float


class RefundOut(BaseModel):
    refund_id: str
    order_id: str
    amount_inr: float
    reason_code: str
    policy_doc_id: str
    status: str
    payment_method: str
    refunded_total_inr: float
    still_refundable_inr: float
    issued_at: str


class CancelOut(BaseModel):
    order_id: str
    status: str
    cancelled_at: str
    payment_method: str
    refund: RefundRecord | None


class AddressOut(BaseModel):
    order_id: str
    status: str
    shipping_city: str
    updated: bool


class ReturnLabelOut(BaseModel):
    label_id: str
    order_id: str
    reason: ReturnReason
    fee_inr: float
    pickup_by: str


class EscalationOut(BaseModel):
    escalation_id: str
    ticket_id: str
    reason: EscalationReason
    priority: Literal["normal", "urgent"]
    respond_within_hours: int
    status: str
