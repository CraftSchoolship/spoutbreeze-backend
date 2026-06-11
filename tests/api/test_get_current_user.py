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
    """No Authorization header, no session cookie → 401."""
    resp = await client.get("/api/payments/subscription")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_authorization_header_path_is_consulted(client, monkeypatch):
    """A Bearer Firebase ID token must reach `verify_id_token`. Stubbed to
    confirm the wiring without involving real Firebase."""
    from app.api import dependencies

    seen_tokens: list[str] = []

    async def fake_verify_id_token(token: str, check_revoked: bool = False):
        seen_tokens.append(token)
        # No `uid` → the dep raises 401 cleanly; we only assert the wiring.
        return {"uid": None}

    monkeypatch.setattr(dependencies._auth_service, "verify_id_token", fake_verify_id_token)

    resp = await client.get(
        "/api/payments/subscription",
        headers={"Authorization": "Bearer header-token-xyz"},
    )
    assert seen_tokens == ["header-token-xyz"], "Authorization header was not consulted by the unified dependency"
    assert resp.status_code == 401  # because `uid` was None


@pytest.mark.anyio
async def test_cookie_path_is_consulted_when_no_header(client, monkeypatch):
    """When no Authorization header is supplied, fall back to the Firebase
    session cookie (`session`), verified via `verify_session_cookie`."""
    from app.api import dependencies

    seen_tokens: list[str] = []

    async def fake_verify_session_cookie(token: str, check_revoked: bool = True):
        seen_tokens.append(token)
        return {"uid": None}

    monkeypatch.setattr(dependencies._auth_service, "verify_session_cookie", fake_verify_session_cookie)

    client.cookies.set("session", "cookie-token-abc")
    try:
        resp = await client.get("/api/payments/subscription")
    finally:
        client.cookies.clear()
    assert seen_tokens == ["cookie-token-abc"]
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_header_takes_precedence_over_cookie(client, monkeypatch):
    """Both supplied → header (ID token) wins over the session cookie."""
    from app.api import dependencies

    id_tokens: list[str] = []
    session_cookies: list[str] = []

    async def fake_verify_id_token(token: str, check_revoked: bool = False):
        id_tokens.append(token)
        return {"uid": None}

    async def fake_verify_session_cookie(token: str, check_revoked: bool = True):
        session_cookies.append(token)
        return {"uid": None}

    monkeypatch.setattr(dependencies._auth_service, "verify_id_token", fake_verify_id_token)
    monkeypatch.setattr(dependencies._auth_service, "verify_session_cookie", fake_verify_session_cookie)

    client.cookies.set("session", "cookie-token-should-be-ignored")
    try:
        resp = await client.get(
            "/api/payments/subscription",
            headers={"Authorization": "Bearer header-wins"},
        )
    finally:
        client.cookies.clear()
    assert id_tokens == ["header-wins"]
    assert session_cookies == [], "session cookie should be ignored when a Bearer header is present"
    assert resp.status_code == 401
