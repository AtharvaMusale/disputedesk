"""Typed request and response models for the tool services."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Order(BaseModel):
    order_id: str
    customer_id: str
    seller_id: str | None
    status: str
    purchase_ts: datetime
    estimated_delivery_ts: datetime
    delivered_ts: datetime | None
    total_usd: float
    freight_usd: float
    item_price_usd: float
    review_score: int | None


class DeliveryEvent(BaseModel):
    event: str
    ts: datetime


class DeliveryEvents(BaseModel):
    order_id: str
    estimated_delivery_ts: datetime
    events: list[DeliveryEvent]
    minutes_late: int | None  # None until delivered; negative means early


class PriorTransaction(BaseModel):
    txn_id: str
    ts: datetime
    amount_usd: float
    merchant: str
    ip: str | None
    device_id: str | None
    disputed: bool


class CustomerHistory(BaseModel):
    customer_id: str
    track: str
    as_of: datetime
    signup_ts: datetime
    tenure_days: int
    orders_total: int
    refunds_30d: int
    refunds_90d: int
    refunds_365d: int
    refund_to_order_ratio: float
    prior_claims_on_time_orders: int
    prior_transactions: list[PriorTransaction]  # fintech only


class SellerStats(BaseModel):
    seller_id: str
    as_of: datetime
    delivered_orders: int
    late_orders: int
    late_rate: float  # 0.0 when there are no deliveries yet


class Payment(BaseModel):
    txn_id: str
    customer_id: str
    ts: datetime
    amount_usd: float
    merchant: str
    descriptor: str
    ip: str | None
    device_id: str | None
    card_reported_lost_at: datetime | None


class PolicyHit(BaseModel):
    policy_id: str
    track: str
    title: str
    text: str
    distance: float  # cosine distance; lower is closer


class RefundRequest(BaseModel):
    case_id: str = Field(min_length=1)
    amount_usd: float = Field(gt=0)
    policy_ids: list[str] = Field(min_length=1)  # a refund must cite the policy it rests on
    reason: str = Field(min_length=1)


class EscalationRequest(BaseModel):
    case_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    policy_ids: list[str] = Field(default_factory=list)


class ActionResult(BaseModel):
    action_id: int
    case_id: str
    kind: Literal["refund", "escalation"]
    amount_usd: float | None
    policy_ids: list[str]
    reason: str
    created_at: datetime
    duplicate: bool  # True when this case already had this action; nothing new was recorded
