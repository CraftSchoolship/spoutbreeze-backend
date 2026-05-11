from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database.session import Base
from app.utils.datetime_utils import utcnow

if TYPE_CHECKING:
    from app.models.user_models import User


class StreamSessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"
    FAILED = "failed"


class StreamSession(Base):
    """Historical record of a broadcast session.

    Redis remains the source of truth for live tracking. This table is written
    best-effort alongside Redis so the admin dashboard can report lifetime and
    period totals beyond the 24h Redis TTL.
    """

    __tablename__ = "stream_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )
    stream_id: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String, default=StreamSessionStatus.ACTIVE.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    user: Mapped[User] = relationship("User")

    __table_args__ = (
        Index("ix_stream_sessions_user_started", "user_id", "started_at"),
        Index("ix_stream_sessions_platform_started", "platform", "started_at"),
        Index("ix_stream_sessions_status", "status"),
    )
