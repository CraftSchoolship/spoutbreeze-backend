from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    """Body for establishing a session from a Firebase ID token.

    The frontend signs in with the Firebase Web SDK, obtains an ID token via
    ``user.getIdToken()``, and posts it here. The backend verifies it and mints
    an httpOnly session cookie. ``first_name`` / ``last_name`` are optional and
    only used on first sign-up (email/password), since Firebase has no separate
    name fields for that provider.
    """

    id_token: str = Field(..., description="Firebase ID token from the Web SDK")
    first_name: str | None = Field(None, description="Optional, set on email/password sign-up")
    last_name: str | None = Field(None, description="Optional, set on email/password sign-up")


class PasswordResetRequest(BaseModel):
    """Body for requesting a self-hosted password-reset email."""

    email: str = Field(..., description="Account email to send the reset link to")


class UserInfo(BaseModel):
    preferred_username: str
    email: str | None = None
    full_name: str | None = None
