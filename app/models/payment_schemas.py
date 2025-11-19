from pydantic import BaseModel, Field, UUID4
from typing import Optional
from datetime import datetime
from app.models.payment_models import (
    SubscriptionPlan,
    SubscriptionStatus,
    TransactionType,
)


# Subscription schemas
class SubscriptionBase(BaseModel):
    plan: SubscriptionPlan


class SubscriptionCreate(SubscriptionBase):
    price_id: str = Field(..., description="Stripe Price ID")


class SubscriptionResponse(BaseModel):
    id: UUID4
    user_id: UUID4
    stripe_customer_id: str
    stripe_subscription_id: Optional[str] = None
    plan: str
    status: str
    trial_start: Optional[datetime] = None
    trial_end: Optional[datetime] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    canceled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PlanLimits(BaseModel):
    max_quality: str
    max_concurrent_streams: Optional[int]
    max_stream_duration_hours: Optional[int]
    support_response_hours: int
    support_channels: list[str]
    chat_filter: bool
    oauth_enabled: bool
    analytics_enabled: bool


class SubscriptionWithLimits(SubscriptionResponse):
    limits: PlanLimits


# Transaction schemas
class TransactionResponse(BaseModel):
    id: UUID4
    subscription_id: UUID4
    stripe_payment_intent_id: str
    stripe_invoice_id: Optional[str] = None
    amount: float
    currency: str
    transaction_type: str
    status: str
    description: Optional[str] = None
    receipt_url: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# Checkout schemas
class CreateCheckoutSessionRequest(BaseModel):
    price_id: str = Field(..., description="Stripe Price ID for the plan")
    success_url: str = Field(
        ..., description="URL to redirect after successful payment"
    )
    cancel_url: str = Field(..., description="URL to redirect after cancelled payment")


class CheckoutSessionResponse(BaseModel):
    session_id: str
    url: str


# Portal schemas
class CustomerPortalRequest(BaseModel):
    return_url: str = Field(
        ..., description="URL to return to after managing subscription"
    )


class CustomerPortalResponse(BaseModel):
    url: str


# Plan schemas
class PlanInfo(BaseModel):
    name: str
    plan_type: SubscriptionPlan
    price: float
    currency: str
    interval: str
    features: list[str]
    limits: PlanLimits
    stripe_price_id: str
    stripe_product_id: str
    is_popular: bool = False


class CancelSubscriptionRequest(BaseModel):
    cancel_immediately: bool = Field(
        default=False,
        description="If true, cancel immediately. If false, cancel at period end",
    )


class UpdateSubscriptionRequest(BaseModel):
    price_id: str = Field(..., description="New Stripe Price ID")


# Webhook schemas
class WebhookEvent(BaseModel):
    type: str
    data: dict
