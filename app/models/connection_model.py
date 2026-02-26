from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import String, DateTime, Text, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.config.database.session import Base

if TYPE_CHECKING:
    from app.models.user_models import User


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, index=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    provider_user_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scopes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(),
        onupdate=lambda: datetime.now(),
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, default=None
    )

    # Relationship
    user: Mapped["User"] = relationship("User", back_populates="connections")

    # Partial unique index: one active (non-revoked) connection per provider per user.
    # Includes provider_user_id so that multiple pages (facebook_page) can coexist.
    __table_args__ = (
        Index(
            "ix_connections_user_provider_active_v2",
            "user_id",
            "provider",
            text("COALESCE(provider_user_id, '')"),
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    # --- Helpers ---

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= datetime.now()

    def get_scopes_list(self) -> list[str]:
        """Parse scopes JSON string into a list."""
        if not self.scopes:
            return []
        try:
            return json.loads(self.scopes)
        except (json.JSONDecodeError, TypeError):
            return []

    def set_scopes_list(self, scopes: list[str]) -> None:
        """Serialize a list of scopes into JSON string."""
        self.scopes = json.dumps(scopes)

    def __repr__(self) -> str:
        return (
            f"<Connection(id={self.id!r}, user_id={self.user_id!r}, "
            f"provider={self.provider!r}, is_active={self.is_active!r}, "
            f"expires_at={self.expires_at!r})>"
        )
