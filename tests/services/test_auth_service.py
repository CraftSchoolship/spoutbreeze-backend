"""Unit tests for the Firebase-backed AuthService.

The Firebase Admin SDK is fully mocked — these tests verify AuthService's
wiring (which SDK call each method makes, and how errors map to HTTPException),
not Firebase itself.
"""

import pytest

import app.services.auth_service as auth_module
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


@pytest.fixture
def svc(monkeypatch):
    # Pretend the Admin SDK is initialised so _ensure_app() passes.
    monkeypatch.setattr(auth_module, "get_firebase_app", lambda: object())
    service = AuthService()
    service._app = object()
    return service


class _FakeUserRecord:
    def __init__(self, uid="uid-1", custom_claims=None):
        self.uid = uid
        self.custom_claims = custom_claims or {}


async def test_verify_id_token_success(monkeypatch, svc):
    monkeypatch.setattr(auth_module.fb_auth, "verify_id_token", lambda t, check_revoked=False: {"uid": "u1"})
    out = await svc.verify_id_token("tok")
    assert out["uid"] == "u1"


async def test_verify_id_token_failure_maps_to_401(monkeypatch, svc):
    def boom(t, check_revoked=False):
        raise ValueError("bad token")

    monkeypatch.setattr(auth_module.fb_auth, "verify_id_token", boom)
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.verify_id_token("tok")
    assert ei.value.status_code == 401


async def test_verify_session_cookie_success(monkeypatch, svc):
    monkeypatch.setattr(
        auth_module.fb_auth,
        "verify_session_cookie",
        lambda c, check_revoked=True: {"uid": "u2", "roles": ["admin"]},
    )
    out = await svc.verify_session_cookie("cookie")
    assert out["uid"] == "u2"
    assert out["roles"] == ["admin"]


async def test_verify_session_cookie_failure_maps_to_401(monkeypatch, svc):
    def boom(c, check_revoked=True):
        raise ValueError("expired")

    monkeypatch.setattr(auth_module.fb_auth, "verify_session_cookie", boom)
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.verify_session_cookie("cookie")
    assert ei.value.status_code == 401


async def test_create_session_cookie_success(monkeypatch, svc):
    captured = {}

    def fake_create(id_token, expires_in=None):
        captured["id_token"] = id_token
        captured["expires_in"] = expires_in
        return "SESSION_COOKIE"

    monkeypatch.setattr(auth_module.fb_auth, "create_session_cookie", fake_create)
    out = await svc.create_session_cookie("idtok")
    assert out == "SESSION_COOKIE"
    assert captured["id_token"] == "idtok"
    assert captured["expires_in"] == auth_module.SESSION_COOKIE_MAX_AGE


async def test_set_roles_claim_preserves_other_claims(monkeypatch, svc):
    captured = {}
    monkeypatch.setattr(
        auth_module.fb_auth,
        "get_user",
        lambda uid: _FakeUserRecord(uid=uid, custom_claims={"foo": "bar"}),
    )
    monkeypatch.setattr(
        auth_module.fb_auth,
        "set_custom_user_claims",
        lambda uid, claims: captured.update({"uid": uid, "claims": claims}),
    )
    await svc.set_roles_claim("uid-1", ["super_admin"])
    assert captured["uid"] == "uid-1"
    assert captured["claims"] == {"foo": "bar", "roles": ["super_admin"]}


async def test_update_user_role_sets_roles_claim(monkeypatch, svc):
    captured = {}
    monkeypatch.setattr(auth_module.fb_auth, "get_user", lambda uid: _FakeUserRecord(uid=uid))
    monkeypatch.setattr(
        auth_module.fb_auth,
        "set_custom_user_claims",
        lambda uid, claims: captured.update({"uid": uid, "claims": claims}),
    )
    await svc.update_user_role("uid-9", "admin")
    assert captured["claims"]["roles"] == ["admin"]


async def test_update_user_profile_maps_fields(monkeypatch, svc):
    captured = {}

    def fake_update(uid, **kwargs):
        captured["uid"] = uid
        captured.update(kwargs)
        return _FakeUserRecord(uid=uid)

    monkeypatch.setattr(auth_module.fb_auth, "update_user", fake_update)
    ok = await svc.update_user_profile(
        "uid-1",
        {"first_name": "F", "last_name": "L", "email": "e@example.com", "ignore": "x"},
    )
    assert ok is True
    assert captured["uid"] == "uid-1"
    assert captured["email"] == "e@example.com"
    assert captured["display_name"] == "F L"


async def test_update_user_profile_noop_when_no_fields(monkeypatch, svc):
    # No recognised fields → no SDK call, returns True.
    called = {"n": 0}

    def fake_update(uid, **kwargs):
        called["n"] += 1

    monkeypatch.setattr(auth_module.fb_auth, "update_user", fake_update)
    ok = await svc.update_user_profile("uid-1", {"unknown": "x"})
    assert ok is True
    assert called["n"] == 0


async def test_delete_user_success(monkeypatch, svc):
    captured = {}
    monkeypatch.setattr(auth_module.fb_auth, "delete_user", lambda uid: captured.update({"uid": uid}))
    ok = await svc.delete_user("uid-1")
    assert ok is True
    assert captured["uid"] == "uid-1"


async def test_delete_user_not_found_is_success(monkeypatch, svc):
    def boom(uid):
        raise auth_module.fb_auth.UserNotFoundError("nope")

    monkeypatch.setattr(auth_module.fb_auth, "delete_user", boom)
    # Already gone → treated as success so DB cleanup proceeds.
    assert await svc.delete_user("uid-1") is True


async def test_logout_revokes_refresh_tokens(monkeypatch, svc):
    captured = {}
    monkeypatch.setattr(
        auth_module.fb_auth, "revoke_refresh_tokens", lambda uid: captured.update({"uid": uid})
    )
    await svc.logout("uid-1")
    assert captured["uid"] == "uid-1"


async def test_logout_is_best_effort(monkeypatch, svc):
    def boom(uid):
        raise RuntimeError("network")

    monkeypatch.setattr(auth_module.fb_auth, "revoke_refresh_tokens", boom)
    # Should not raise — logout clears the cookie regardless.
    await svc.logout("uid-1")


async def test_health_check(monkeypatch):
    monkeypatch.setattr(auth_module, "get_firebase_app", lambda: object())
    service = AuthService()
    assert await service.health_check() is True


async def test_health_check_unconfigured(monkeypatch):
    monkeypatch.setattr(auth_module, "get_firebase_app", lambda: None)
    service = AuthService()
    service._app = None
    assert await service.health_check() is False
