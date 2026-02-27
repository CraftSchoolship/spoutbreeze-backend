import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

import app.services.auth_service as auth_module
from app.services.auth_service import AuthService


class Resp:
    """Mimics httpx.Response for test mocking."""
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}",
                request=MagicMock(),
                response=self,
            )

    def json(self):
        return self._json


@pytest.fixture
def fake_settings():
    class S:
        keycloak_client_id = "client-id"
        keycloak_server_url = "https://kc.example.com"
        keycloak_admin_username = "admin"
        keycloak_admin_password = "secret"
        keycloak_realm = "spoutbreeze"

    return S()


class FakeKC:
    def __init__(self):
        self._public_key = "FAKEPUBKEY"  # no PEM header, triggers PEM formatting
        self._token = {"access_token": "acc", "refresh_token": "ref", "expires_in": 300}
        self._userinfo = {"preferred_username": "alice"}
        self._well_known = {"authorization_endpoint": "https://kc/auth"}
        self.logout_called_with = None

    def public_key(self):
        return self._public_key

    def token(self, **kwargs):
        return self._token

    def refresh_token(self, refresh_token):
        return self._token

    def userinfo(self, access_token):
        return self._userinfo

    def well_known(self):
        return self._well_known

    def logout(self, refresh_token):
        self.logout_called_with = refresh_token


@pytest.fixture
def make_service(monkeypatch, fake_settings):
    def _factory(fake_kc=None, exists=False):
        kc = fake_kc or FakeKC()
        monkeypatch.setattr(auth_module, "get_settings", lambda: fake_settings)
        monkeypatch.setattr(auth_module, "keycloak_openid", kc)
        # Prevent touching the filesystem for cert detection
        monkeypatch.setattr(auth_module.os.path, "exists", lambda p: exists)
        return AuthService(), kc

    return _factory


def test_validate_token_success(monkeypatch, make_service):
    svc, _ = make_service()
    payload = {"preferred_username": "bob", "sub": "123"}
    monkeypatch.setattr(auth_module.jwt, "decode", lambda *a, **k: payload)
    out = svc.validate_token("Bearer abc")
    assert out is payload


def test_validate_token_missing_username(monkeypatch, make_service):
    svc, _ = make_service()
    monkeypatch.setattr(auth_module.jwt, "decode", lambda *a, **k: {"sub": "123"})
    with pytest.raises(auth_module.HTTPException) as ei:
        svc.validate_token("t")
    assert ei.value.status_code == 401
    assert "missing username" in ei.value.detail


def test_validate_token_decode_error(monkeypatch, make_service):
    svc, _ = make_service()

    def boom(*a, **k):
        raise Exception("bad sig")

    monkeypatch.setattr(auth_module.jwt, "decode", boom)
    with pytest.raises(auth_module.HTTPException) as ei:
        svc.validate_token("t")
    assert ei.value.status_code == 401
    assert "bad sig" in ei.value.detail


def test_exchange_token_success(make_service):
    svc, kc = make_service()
    kc._token = {"access_token": "x"}
    out = svc.exchange_token("code", "http://r", "ver")
    assert out["access_token"] == "x"


def test_exchange_token_failure(monkeypatch, make_service):
    svc, kc = make_service()
    kc.token = lambda **k: (_ for _ in ()).throw(Exception("oops"))
    with pytest.raises(auth_module.HTTPException) as ei:
        svc.exchange_token("c", "u", "v")
    assert ei.value.status_code == 400


def test_refresh_token_success(make_service):
    svc, kc = make_service()
    kc._token = {"access_token": "A1", "refresh_token": "R1", "expires_in": 123}
    kc._userinfo = {"preferred_username": "alice"}
    out = svc.refresh_token("R0")
    assert out["access_token"] == "A1"
    assert out["refresh_token"] == "R1"
    assert out["expires_in"] == 123
    assert out["user_info"]["preferred_username"] == "alice"


def test_refresh_token_failure(make_service):
    svc, kc = make_service()
    kc.refresh_token = lambda rt: (_ for _ in ()).throw(Exception("nope"))
    with pytest.raises(auth_module.HTTPException) as ei:
        svc.refresh_token("R0")
    assert ei.value.status_code == 401


def test_get_user_info_success(make_service):
    svc, kc = make_service()
    kc._userinfo = {"preferred_username": "z"}
    assert svc.get_user_info("A")["preferred_username"] == "z"


def test_get_user_info_failure(make_service):
    svc, kc = make_service()
    kc.userinfo = lambda at: (_ for _ in ()).throw(Exception("x"))
    with pytest.raises(auth_module.HTTPException) as ei:
        svc.get_user_info("A")
    assert ei.value.status_code == 401


@pytest.mark.anyio
async def test_get_admin_token_caches(monkeypatch, make_service):
    svc, _ = make_service()
    calls = {"count": 0}

    async def fake_post(self_client, url, **kwargs):
        calls["count"] += 1
        return Resp(200, {"access_token": f"adm{calls['count']}", "expires_in": 60})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    t1 = await svc._get_admin_token()
    t2 = await svc._get_admin_token()
    assert t1 == "adm1" and t2 == "adm1"
    assert calls["count"] == 1  # cached on second call


@pytest.mark.anyio
async def test_update_user_profile_success(monkeypatch, make_service):
    svc, _ = make_service()
    monkeypatch.setattr(svc, "_get_admin_token", AsyncMock(return_value="ADM"))
    captured = {}

    async def fake_put(self_client, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return Resp(204, {})

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    ok = await svc.update_user_profile(
        "user-1",
        {
            "first_name": "F",
            "last_name": "L",
            "email": "e@example.com",
            "username": "u",
            "ignore_me": "x",
        },
    )
    assert ok is True
    assert captured["json"] == {
        "firstName": "F",
        "lastName": "L",
        "email": "e@example.com",
        "username": "u",
    }
    assert "Authorization" in captured["headers"]


@pytest.mark.anyio
async def test_update_user_profile_timeout(monkeypatch, make_service):
    svc, _ = make_service()
    monkeypatch.setattr(svc, "_get_admin_token", AsyncMock(return_value="ADM"))

    async def fake_put(self_client, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.update_user_profile("user-1", {})
    assert ei.value.status_code == 408


@pytest.mark.anyio
async def test_update_user_profile_request_exception(monkeypatch, make_service):
    svc, _ = make_service()
    monkeypatch.setattr(svc, "_get_admin_token", AsyncMock(return_value="ADM"))

    async def fake_put(self_client, url, **kwargs):
        return Resp(400, text="bad")

    monkeypatch.setattr(httpx.AsyncClient, "put", fake_put)
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.update_user_profile("user-1", {})
    assert ei.value.status_code == 400


def test_logout_success(make_service):
    svc, kc = make_service()
    svc.logout("R")
    assert kc.logout_called_with == "R"


def test_logout_failure(make_service):
    svc, kc = make_service()
    kc.logout = lambda refresh_token: (_ for _ in ()).throw(Exception("boom"))
    with pytest.raises(auth_module.HTTPException) as ei:
        svc.logout("R")
    assert ei.value.status_code == 400


def test_health_check(monkeypatch, make_service):
    svc, kc = make_service()
    assert svc.health_check() is True
    kc.well_known = lambda: (_ for _ in ()).throw(Exception("down"))
    assert svc.health_check() is False


@pytest.mark.anyio
async def test_get_client_id_success(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_get(self_client, url, **kwargs):
        assert kwargs.get("params") == {"clientId": "spoutbreezeAPI"}
        return Resp(200, [{"id": "cid-1"}])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await svc._get_client_id("ADM", "spoutbreezeAPI")
    assert out == "cid-1"


@pytest.mark.anyio
async def test_get_client_id_not_found(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_get(self_client, url, **kwargs):
        return Resp(200, [])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    with pytest.raises(ValueError):
        await svc._get_client_id("ADM", "missing")


@pytest.mark.anyio
async def test_get_client_role_success(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_get(self_client, url, **kwargs):
        return Resp(200, {"id": "rid", "name": "roleA"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await svc._get_client_role("ADM", "cid", "roleA")
    assert out["name"] == "roleA"


@pytest.mark.anyio
async def test_get_user_client_roles_success(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_get(self_client, url, **kwargs):
        return Resp(200, [{"name": "oldRole"}])

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await svc._get_user_client_roles("ADM", "uid", "cid")
    assert out and out[0]["name"] == "oldRole"


@pytest.mark.anyio
async def test_get_user_client_roles_exception_returns_empty(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_get(self_client, url, **kwargs):
        raise httpx.ConnectError("net")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await svc._get_user_client_roles("ADM", "uid", "cid")
    assert out == []


@pytest.mark.anyio
async def test_remove_user_client_roles_noop(monkeypatch, make_service):
    svc, _ = make_service()
    called = {"del": False}

    async def fake_request(self_client, method, url, **kwargs):
        called["del"] = True
        return Resp(204)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    await svc._remove_user_client_roles("ADM", "uid", "cid", [])
    assert called["del"] is False


@pytest.mark.anyio
async def test_remove_user_client_roles_success(monkeypatch, make_service):
    svc, _ = make_service()
    called = {"json": None}

    async def fake_request(self_client, method, url, **kwargs):
        called["json"] = kwargs.get("json")
        return Resp(204)

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    await svc._remove_user_client_roles("ADM", "uid", "cid", [{"name": "old"}])
    assert called["json"] == [{"name": "old"}]


@pytest.mark.anyio
async def test_assign_user_client_role_success(monkeypatch, make_service):
    svc, _ = make_service()
    captured = {"json": None}

    async def fake_post(self_client, url, **kwargs):
        captured["json"] = kwargs.get("json")
        return Resp(204)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    await svc._assign_user_client_role("ADM", "uid", "cid", {"name": "roleX"})
    assert captured["json"] == [{"name": "roleX"}]


@pytest.mark.anyio
async def test_update_user_role_happy_path(monkeypatch, make_service):
    svc, _ = make_service()
    called = {"removed": None, "assigned": None}
    monkeypatch.setattr(svc, "_get_admin_token", AsyncMock(return_value="ADM"))
    monkeypatch.setattr(svc, "_get_client_id", AsyncMock(return_value="cid-1"))
    monkeypatch.setattr(
        svc, "_get_user_client_roles", AsyncMock(return_value=[{"name": "old"}])
    )

    async def fake_remove(adm, uid, cid, roles):
        called["removed"] = roles

    monkeypatch.setattr(svc, "_remove_user_client_roles", fake_remove)
    monkeypatch.setattr(
        svc, "_get_client_role", AsyncMock(return_value={"id": "rid", "name": "admin"})
    )

    async def fake_assign(adm, uid, cid, role):
        called["assigned"] = role["name"]

    monkeypatch.setattr(svc, "_assign_user_client_role", fake_assign)
    await svc.update_user_role("uid-1", "admin")
    assert called["removed"] == [{"name": "old"}]
    assert called["assigned"] == "admin"


@pytest.mark.anyio
async def test_update_user_role_failure_wrapped(monkeypatch, make_service):
    svc, _ = make_service()
    monkeypatch.setattr(
        svc, "_get_admin_token", AsyncMock(side_effect=Exception("fail"))
    )
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.update_user_role("uid", "role")
    assert ei.value.status_code == 500
