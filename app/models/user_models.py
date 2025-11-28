from __future__ import annotations
import uuid
from datetime import datetime
from typing import List, TYPE_CHECKING, Optional

from sqlalchemy import String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.config.database.session import Base
from app.models.bbb_models import BbbMeeting
from app.models.channel.channels_model import Channel
from app.models.stream_models import RtmpEndpoint
from app.models.event.event_models import Event

if TYPE_CHECKING:
    from app.models.twitch.twitch_models import TwitchToken
    from app.models.payment_models import Subscription


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )
    keycloak_id: Mapped[str] = mapped_column(
        String, unique=True, index=True, nullable=False
    )
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    roles: Mapped[str] = mapped_column(String, default="moderator", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now(), onupdate=datetime.now(), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # Relationships – note the use of fully qualified names if needed or move to __init__.py import order
    rtmp_endpoints: Mapped[list[RtmpEndpoint]] = relationship(
        "RtmpEndpoint", back_populates="user", cascade="all, delete-orphan"
    )
    bbb_meetings: Mapped[list[BbbMeeting]] = relationship(
        "BbbMeeting", back_populates="user", cascade="all, delete-orphan"
    )
    channels: Mapped[list[Channel]] = relationship(
        "app.models.channel.channels_model.Channel",
        back_populates="creator",
        cascade="all, delete-orphan",
    )
    created_events: Mapped[list[Event]] = relationship(
        "app.models.event.event_models.Event",
        back_populates="creator",
        cascade="all, delete-orphan",
    )
    organized_events: Mapped[list[Event]] = relationship(
        "app.models.event.event_models.Event",
        secondary="user_event_association",
        back_populates="organizers",
        cascade_backrefs=False,
        passive_deletes=True,
    )
    twitch_tokens: Mapped[List["TwitchToken"]] = relationship(
        "TwitchToken", back_populates="user", cascade="all, delete-orphan"
    )
    subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    def get_roles_list(self) -> List[str]:
        """Get roles as a list from comma-separated string"""
        if not self.roles:
            return []
        return [role.strip() for role in self.roles.split(",") if role.strip()]

    def set_roles_list(self, roles: List[str]) -> None:
        """Set roles from a list to comma-separated string"""
        if roles:  # Only update if roles is not empty
            self.roles = ",".join(roles)
        # If roles is empty, preserves default

    def has_role(self, role: str) -> bool:
        """Check if user has a specific role"""
        return role in self.get_roles_list()

    def has_any_role(self, *roles: str) -> bool:
        """Check if user has any of the specified roles"""
        user_roles = self.get_roles_list()
        return any(role in user_roles for role in roles)

    def is_admin(self) -> bool:
        """Check if user has admin role"""
        return self.has_role("admin")

    def is_moderator(self) -> bool:
        """Check if user has moderator role"""
        return self.has_role("moderator")

    def get_active_twitch_token(self) -> Optional["TwitchToken"]:
        """Get the user's active Twitch token"""
        from datetime import datetime

        for token in self.twitch_tokens:
            if token.is_active and token.expires_at > datetime.now():
                return token
        return None

    def __repr__(self) -> str:
        return f"<User(id={self.id!r}, username={self.username!r}, email={self.email!r}, roles={self.roles!r})>"
