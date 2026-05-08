from pydantic import BaseModel, Field


class TokenRequest(BaseModel):
    """
    Model for token exchange request
    """

    code: str = Field(..., description="Authorization code from Keycloak")
    redirect_uri: str = Field(..., description="Redirect URI used in the authorization request")
    code_verifier: str = Field(..., description="Code verifier used in the authorization request")


class TokenResponse(BaseModel):
    """
    Model for token exchange response
    """

    access_token: str
    expires_in: int
    refresh_token: str
    refresh_expires_in: int | None = None
    token_type: str = "Bearer"
    user_info: dict


class User(BaseModel):
    """
    Model for user information
    """

    username: str
    password: str
    email: str
    first_name: str
    last_name: str


class UserInfo(BaseModel):
    preferred_username: str
    email: str | None = None
    full_name: str | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token to obtain new access token")


class LogoutRequest(BaseModel):
    """
    Model for logout request
    """

    refresh_token: str


class DevTokenRequest(BaseModel):
    """Body for the development-only password-grant endpoint.

    Credentials are accepted in the request body (not as query parameters)
    so they don't end up in access logs, browser history, or proxy logs.
    """

    username: str = Field(..., description="Keycloak username")
    password: str = Field(..., description="Keycloak password")
