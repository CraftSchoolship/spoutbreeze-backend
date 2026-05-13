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


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool
    email_domains: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssignUserOrganizationRequest(BaseModel):
    organization_id: UUID | None
