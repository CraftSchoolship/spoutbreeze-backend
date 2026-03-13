import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database.session import Base

if TYPE_CHECKING:
    from app.models.user_models import User


class BbbMeeting(Base):
    __tablename__ = "bbb_meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )
    meeting_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    internal_meeting_id: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    parent_meeting_id: Mapped[str | None] = mapped_column(String, index=True)
    attendee_pw: Mapped[str] = mapped_column(String, index=True, nullable=False)
    moderator_pw: Mapped[str] = mapped_column(String, index=True, nullable=False)
    create_time: Mapped[str | None] = mapped_column(String, index=True)
    voice_bridge: Mapped[str | None] = mapped_column(String, index=True)
    dial_number: Mapped[str | None] = mapped_column(String, index=True)
    has_user_joined: Mapped[str | None] = mapped_column(String, index=True)
    duration: Mapped[str | None] = mapped_column(String, index=True)
    has_been_forcibly_ended: Mapped[str | None] = mapped_column(String, index=True)
    message_key: Mapped[str | None] = mapped_column(String, index=True)
    message: Mapped[str | None] = mapped_column(String, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="bbb_meetings")
