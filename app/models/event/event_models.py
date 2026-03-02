from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database.session import Base
from app.models.channel.channels_model import Channel

if TYPE_CHECKING:
    from app.models.user_models import User


class EventStatus(enum.Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    ENDED = "ended"
    CANCELLED = "cancelled"


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    occurs: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now(), onupdate=datetime.now(), nullable=False)

    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("channels.id", ondelete="CASCADE"),
        nullable=False,
    )
    meeting_id: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    moderator_pw: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    attendee_pw: Mapped[str | None] = mapped_column(String, nullable=True, unique=True)
    meeting_created: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), default=EventStatus.SCHEDULED, nullable=False)
    actual_start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Use string references instead of direct class references
    creator: Mapped[User] = relationship(
        "app.models.user_models.User",
        back_populates="created_events",
    )
    channel: Mapped[Channel] = relationship(
        "app.models.channel.channels_model.Channel",
        back_populates="events",
    )
    organizers: Mapped[list[User]] = relationship(
        "app.models.user_models.User",
        secondary="user_event_association",
        back_populates="organized_events",
        cascade_backrefs=False,
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Event(id={self.id!r}, title={self.title!r})>"
