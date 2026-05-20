from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.database.session import Base

if TYPE_CHECKING:
    from app.models.user_models import User


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    users: Mapped[list[User]] = relationship("User", back_populates="organization", passive_deletes=True)
    email_domains: Mapped[list[OrganizationEmailDomain]] = relationship(
        "OrganizationEmailDomain",
        back_populates="organization",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Organization(id={self.id!r}, name={self.name!r})>"


class OrganizationEmailDomain(Base):
    __tablename__ = "organization_email_domains"

    domain: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verification_token: Mapped[str | None] = mapped_column(String, nullable=True)
    verification_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped[Organization] = relationship("Organization", back_populates="email_domains")

    def __repr__(self) -> str:
        return (
            f"<OrganizationEmailDomain(domain={self.domain!r}, "
            f"org_id={self.organization_id!r}, verified={self.verified_at is not None})>"
        )


class OrganizationInvite(Base):
    __tablename__ = "organization_invites"

    code: Mapped[str] = mapped_column(String, primary_key=True)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    organization: Mapped[Organization] = relationship("Organization")

    def __repr__(self) -> str:
        return (
            f"<OrganizationInvite(code={self.code!r}, org_id={self.organization_id!r}, "
            f"revoked={self.revoked_at is not None})>"
        )
