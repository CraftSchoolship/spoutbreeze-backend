from __future__ import annotations
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from enum import Enum

from sqlalchemy import String, DateTime, Boolean, Integer, Float, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import ForeignKey
from app.config.database.session import Base

if TYPE_CHECKING:
    from app.models.user_models import User


class SubscriptionPlan(str, Enum):
    """Subscription plan types"""
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(str, Enum):
    """Subscription status types"""
    ACTIVE = "active"
    TRIALING = "trialing"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    INCOMPLETE_EXPIRED = "incomplete_expired"
    PAST_DUE = "past_due"
    UNPAID = "unpaid"


class Subscription(Base):
    """User subscription model"""
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )
    
    # User reference
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Stripe references
    stripe_customer_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    stripe_subscription_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, unique=True, index=True)
    stripe_price_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    stripe_product_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Subscription details
    plan: Mapped[str] = mapped_column(String, default=SubscriptionPlan.FREE.value, nullable=False)
    status: Mapped[str] = mapped_column(String, default=SubscriptionStatus.TRIALING.value, nullable=False)
    
    # Trial information
    trial_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    trial_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Subscription period
    current_period_start: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    current_period_end: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Cancellation
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow, 
        nullable=False
    )
    
    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="subscription")
    transactions: Mapped[list["Transaction"]] = relationship(
        "Transaction", 
        back_populates="subscription", 
        cascade="all, delete-orphan"
    )

    def is_active(self) -> bool:
        """Check if subscription is active or trialing"""
        return self.status in [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]
    
    def is_trial(self) -> bool:
        """Check if subscription is in trial period"""
        return self.status == SubscriptionStatus.TRIALING.value
    
    def get_plan_limits(self) -> dict:
        """Get plan limits based on current plan"""
        if self.plan == SubscriptionPlan.FREE.value:
            return {
                "max_quality": "720p",
                "max_concurrent_streams": 1,
                "max_stream_duration_hours": 1,
                "support_response_hours": 72,
                "support_channels": ["email"],
                "chat_filter": False,
                "oauth_enabled": False,
                "analytics_enabled": False,
            }
        elif self.plan == SubscriptionPlan.PRO.value:
            return {
                "max_quality": "1080p",
                "max_concurrent_streams": 10,
                "max_stream_duration_hours": None,  # Unlimited
                "support_response_hours": 24,
                "support_channels": ["email", "chat"],
                "chat_filter": False,
                "oauth_enabled": False,
                "analytics_enabled": False,
            }
        elif self.plan == SubscriptionPlan.ENTERPRISE.value:
            return {
                "max_quality": "4K",
                "max_concurrent_streams": None,  # Unlimited
                "max_stream_duration_hours": None,  # Unlimited
                "support_response_hours": 0,  # 24/7
                "support_channels": ["email", "chat"],
                "chat_filter": True,
                "oauth_enabled": True,
                "analytics_enabled": True,
            }
        return {}


class TransactionType(str, Enum):
    """Transaction type enum"""
    PAYMENT = "payment"
    REFUND = "refund"
    FAILED = "failed"


class Transaction(Base):
    """Payment transaction model"""
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )
    
    # Subscription reference
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    
    # Stripe references
    stripe_payment_intent_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    stripe_invoice_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Transaction details
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="usd", nullable=False)
    transaction_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    
    # Additional info
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    receipt_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    subscription: Mapped["Subscription"] = relationship("Subscription", back_populates="transactions")
