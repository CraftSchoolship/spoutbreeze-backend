"""
Notification system SQLAlchemy models.

Tables:
  - notifications:              stores every notification instance
  - notification_preferences:   per-user delivery preferences (email, push, in-app)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database.session import Base

if TYPE_CHECKING:
    from app.models.user_models import User


# ---------------------------------------------------------------------------
# Notification type enum — extensible via Alembic ALTER TYPE migrations
# ---------------------------------------------------------------------------
class NotificationType(str, Enum):
    """Canonical set of notification types across SpoutBreeze."""

    # Streaming
    STREAM_STARTED = "stream_started"
    STREAM_ENDED = "stream_ended"
    STREAM_ERROR = "stream_error"

    # Events
    EVENT_CREATED = "event_created"
    EVENT_STARTING_SOON = "event_starting_soon"
    EVENT_CANCELLED = "event_cancelled"
    ORGANIZER_ADDED = "organizer_added"
    EVENT_REMINDER = "event_reminder"

    # Payments
    PAYMENT_SUCCESS = "payment_success"
    PAYMENT_FAILED = "payment_failed"
    SUBSCRIPTION_EXPIRING = "subscription_expiring"

    # System
    SYSTEM_ANNOUNCEMENT = "system_announcement"
    ACCOUNT_UPDATE = "account_update"

    # Chat
    CHAT_MENTION = "chat_mention"


class NotificationPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class DeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Notification model
# ---------------------------------------------------------------------------
class Notification(Base):
    """
    Central notification record.  One row per notification per recipient.
    Delivery channels are tracked via boolean flags + per-channel status.
    """

    __tablename__ = "notifications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )

    # Recipient
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Content
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON blob for extra payload
    priority: Mapped[str] = mapped_column(String(16), default=NotificationPriority.NORMAL.value, nullable=False)

    # Delivery channel flags (set at creation time)
    send_in_app: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    send_email: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    send_push: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Per-channel delivery status
    in_app_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.PENDING.value, nullable=False)
    email_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.SKIPPED.value, nullable=False)
    push_status: Mapped[str] = mapped_column(String(16), default=DeliveryStatus.SKIPPED.value, nullable=False)

    # Read/unread state
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Retry tracking
    email_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    push_retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Deduplication key — prevents duplicate notifications within a window
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    # Relationship
    user: Mapped[User] = relationship("User", back_populates="notifications")

    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
        Index("ix_notifications_user_type", "user_id", "notification_type"),
        Index("ix_notifications_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# User notification preferences
# ---------------------------------------------------------------------------
class NotificationPreference(Base):
    """
    Per-user, per-notification-type delivery preference.
    Missing rows fall back to defaults (in_app=True, email=False, push=False).
    """

    __tablename__ = "notification_preferences"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)

    # Channel toggles
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    # Relationship
    user: Mapped[User] = relationship("User", back_populates="notification_preferences")

    __table_args__ = (
        Index(
            "ix_notification_preferences_user_type",
            "user_id",
            "notification_type",
            unique=True,
        ),
    )
