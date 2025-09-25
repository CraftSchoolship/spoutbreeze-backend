from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from datetime import datetime
from typing import Optional
from uuid import UUID


class UserBase(BaseModel):
    username: str
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class UserCreate(UserBase):
    keycloak_id: str
    roles: str


class UserResponse(UserBase):
    id: UUID
    keycloak_id: str
    roles: str
    created_at: Optional[datetime] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class UpdateProfileRequest(BaseModel):
    email: Optional[EmailStr] = Field(None, min_length=1, max_length=50)
    first_name: Optional[str] = Field(None, min_length=1, max_length=50)
    last_name: Optional[str] = Field(None, min_length=1, max_length=50)

    @field_validator("email")
    def validate_email(cls, v):
        if v is not None:
            import re

            if not re.match(r"^[^@]+@[^@]+\.[^@]+$", v):
                raise ValueError("Invalid email format")
        return v

    model_config = ConfigDict(from_attributes=True)


class UpdateUserRoleRequest(BaseModel):
    role: str = Field(
        ..., min_length=1, max_length=50, description="The new role for the user"
    )

    @field_validator("role")
    def validate_role(cls, v):
        if not v or not v.strip():
            raise ValueError("Role cannot be empty")
        return v.strip().lower()
