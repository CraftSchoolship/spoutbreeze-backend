"""Tests for the unified `get_current_user` dependency.

The two former copies (one in `user_controller.py`, one in `payment_controller.py`)
diverged on token source — cookie-only vs. header-or-cookie. After
consolidation, the single dependency must accept both, and the legacy
re-export paths must keep working so the dozen-plus existing imports across
the codebase don't break.
"""

import pytest

from app.api.dependencies import get_current_user as canonical_dep
from app.controllers.payment_controller import get_current_user as payment_dep
from app.controllers.user_controller import get_current_user as user_dep


def test_payment_and_user_imports_resolve_to_the_same_function():
    """The point of the consolidation: both legacy import paths must
    return the exact same callable so any auth tightening lands once."""
    assert user_dep is canonical_dep
    assert payment_dep is canonical_dep


@pytest.mark.anyio
async def test_no_token_returns_401(client):
    """No Authorization header, no access_token cookie → 401."""
    resp = await client.get("/api/payments/subscription")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_authorization_header_path_is_consulted(client, monkeypatch):
    """Header-supplied Bearer token must reach the validator. Validator is
    stubbed to confirm the wiring without involving real Keycloak."""
    from app.api import dependencies

    seen_tokens: list[str] = []

    async def fake_validate(token: str):
        seen_tokens.append(token)
        # Return a payload with no `sub` so the dep raises 401 cleanly —
        # we only care here that the header path was taken.
        return {"sub": None}

    monkeypatch.setattr(dependencies._auth_service, "validate_token", fake_validate)

    resp = await client.get(
        "/api/payments/subscription",
        headers={"Authorization": "Bearer header-token-xyz"},
    )
    assert seen_tokens == ["header-token-xyz"], "Authorization header was not consulted by the unified dependency"
    assert resp.status_code == 401  # because `sub` was None


@pytest.mark.anyio
async def test_cookie_path_is_consulted_when_no_header(client, monkeypatch):
    """When no Authorization header is supplied, fall back to the
    access_token cookie."""
    from app.api import dependencies

    seen_tokens: list[str] = []

    async def fake_validate(token: str):
        seen_tokens.append(token)
        return {"sub": None}

    monkeypatch.setattr(dependencies._auth_service, "validate_token", fake_validate)

    client.cookies.set("access_token", "cookie-token-abc")
    try:
        resp = await client.get("/api/payments/subscription")
    finally:
        client.cookies.clear()
    assert seen_tokens == ["cookie-token-abc"]
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_header_takes_precedence_over_cookie(client, monkeypatch):
    """Both supplied → header wins. Matters for cross-origin API callers
    that may also have a stale cookie sitting around."""
    from app.api import dependencies

    seen_tokens: list[str] = []

    async def fake_validate(token: str):
        seen_tokens.append(token)
        return {"sub": None}

    monkeypatch.setattr(dependencies._auth_service, "validate_token", fake_validate)

    client.cookies.set("access_token", "cookie-token-should-be-ignored")
    try:
        resp = await client.get(
            "/api/payments/subscription",
            headers={"Authorization": "Bearer header-wins"},
        )
    finally:
        client.cookies.clear()
    assert seen_tokens == ["header-wins"]
    assert resp.status_code == 401
