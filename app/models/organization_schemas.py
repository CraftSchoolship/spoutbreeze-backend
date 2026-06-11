from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")


def _normalize_domains(value: list[str] | None) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in value:
        d = (raw or "").strip().lower().lstrip("@")
        if not d:
            continue
        if not _DOMAIN_RE.match(d):
            raise ValueError(f"Invalid email domain: {raw!r}")
        if d in seen:
            continue
        seen.add(d)
        out.append(d)
    return out


class OrganizationCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    email_domains: list[str] = Field(default_factory=list)

    @field_validator("name")
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("email_domains")
    def _normalize(cls, v: list[str]) -> list[str]:
        return _normalize_domains(v)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    email_domains: list[str] | None = None
    is_active: bool | None = None

    @field_validator("name")
    def _strip_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("email_domains")
    def _normalize(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        return _normalize_domains(v)


class EmailDomainDetail(BaseModel):
    domain: str
    verified: bool
    verified_at: datetime | None = None
    verification_record_name: str | None = None
    verification_record_value: str | None = None


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool
    email_domains: list[str]
    # Per-domain verification view. Populated by `_serialize_org` in the
    # controllers; the legacy `email_domains` field above is kept as a simple
    # string list for the super-admin dashboard.
    email_domain_details: list[EmailDomainDetail] = []
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignUserOrganizationRequest(BaseModel):
    organization_id: UUID | None


def _normalize_single_domain(v: str) -> str:
    d = (v or "").strip().lower().lstrip("@")
    if not d:
        raise ValueError("email_domain cannot be empty")
    if not _DOMAIN_RE.match(d):
        raise ValueError(f"Invalid email domain: {v!r}")
    return d


class CreateMyOrgRequest(BaseModel):
    """Self-serve org creation by a non-super-admin user."""

    name: str = Field(..., min_length=1, max_length=255)
    email_domain: str = Field(..., min_length=1, max_length=253)

    @field_validator("name")
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v

    @field_validator("email_domain")
    def _normalize_domain(cls, v: str) -> str:
        return _normalize_single_domain(v)


class JoinOrgRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)

    @field_validator("code")
    def _strip_code(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("code cannot be empty")
        return v


class AddDomainRequest(BaseModel):
    """Org admin adding an additional email domain to their existing org."""

    domain: str = Field(..., min_length=1, max_length=253)

    @field_validator("domain")
    def _normalize_domain(cls, v: str) -> str:
        return _normalize_single_domain(v)


class DomainVerificationStatus(BaseModel):
    """One row in the email_domains list with verification metadata."""

    domain: str
    verified: bool
    verification_token: str | None
    verification_record_name: str | None
    verification_record_value: str | None


class CreateMyOrgResponse(BaseModel):
    organization: OrganizationResponse
    verification: DomainVerificationStatus
    # The caller was just granted the `admin` role as a Firebase custom claim.
    # Their current session cookie predates the claim, so the frontend must
    # force-refresh the ID token and re-establish the session before relying
    # on the new role (e.g. navigating to /my-org).
    session_refresh_required: bool = True


class OrganizationInviteResponse(BaseModel):
    code: str
    organization_id: UUID
    created_at: datetime
    revoked_at: datetime | None
    expires_at: datetime | None
    join_path: str  # e.g. "/join/org/<code>"

    model_config = ConfigDict(from_attributes=True)


class JoinOrgResponse(BaseModel):
    organization: OrganizationResponse
