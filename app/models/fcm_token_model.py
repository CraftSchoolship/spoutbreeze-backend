"""
FCM (Firebase Cloud Messaging) device-token model.

Each row links a user to one FCM registration token (one per browser / device).
A user may have multiple tokens if they use multiple browsers or machines.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database.session import Base

if TYPE_CHECKING:
    from app.models.user_models import User


class FCMToken(Base):
    """Stores FCM registration tokens per user per device/browser."""

    __tablename__ = "fcm_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token: Mapped[str] = mapped_column(
        String(512),
        unique=True,
        nullable=False,
    )

    device_info: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    # Relationship
    user: Mapped[User] = relationship("User", back_populates="fcm_tokens")

    __table_args__ = (Index("ix_fcm_tokens_user_id", "user_id"),)
