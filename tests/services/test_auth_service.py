import httpx
import pytest

import app.services.auth_service as auth_module
from app.services.auth_service import AuthService

pytestmark = pytest.mark.asyncio


class Resp:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
        self.request = httpx.Request("GET", "https://kc.example.com/")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=self.request, response=httpx.Response(self.status_code))

    def json(self):
        return self._json


class FakeAsyncClient:
    """Stand-in for httpx.AsyncClient that supports the async context-manager protocol."""

    def __init__(self, handlers=None):
        self.handlers = handlers or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def _dispatch(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        handler = self.handlers.get(method)
        if handler is None:
            return Resp(204, {})
        result = handler(url, **kwargs)
        return result

    async def post(self, url, **kwargs):
        return await self._dispatch("post", url, **kwargs)

    async def get(self, url, **kwargs):
        return await self._dispatch("get", url, **kwargs)

    async def put(self, url, **kwargs):
        return await self._dispatch("put", url, **kwargs)

    async def delete(self, url, **kwargs):
        return await self._dispatch("delete", url, **kwargs)

    async def request(self, method, url, **kwargs):
        return await self._dispatch(method.lower(), url, **kwargs)


@pytest.fixture
def fake_settings():
    class S:
        keycloak_client_id = "client-id"
        keycloak_server_url = "https://kc.example.com"
        keycloak_admin_username = "admin"
        keycloak_admin_password = "secret"
        keycloak_realm = "spoutbreeze"
        # SSL verification off for tests — they don't hit Keycloak, and
        # this avoids resolve_ssl_verify probing the filesystem.
        ssl_verify = False
        ssl_cert_file = None

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
    def _factory(fake_kc=None):
        kc = fake_kc or FakeKC()
        monkeypatch.setattr(auth_module, "get_settings", lambda: fake_settings)
        # The service now resolves the Keycloak client through a factory.
        # Patch the factory so calls return our FakeKC without ever
        # instantiating a real python-keycloak client.
        monkeypatch.setattr(auth_module, "get_keycloak_openid", lambda: kc)
        return AuthService(), kc

    return _factory


def _patch_http_client(monkeypatch, svc, fake_client):
    monkeypatch.setattr(svc, "_http_client", lambda: fake_client)


async def test_validate_token_success(monkeypatch, make_service):
    svc, _ = make_service()
    payload = {"preferred_username": "bob", "sub": "123"}

    captured = {}

    def fake_decode(*a, **k):
        captured.update(k)
        return payload

    monkeypatch.setattr(auth_module.jwt, "decode", fake_decode)
    out = await svc.validate_token("Bearer abc")
    assert out is payload
    # Audience must be passed and verification must be enabled — these
    # are the guarantees the JWT-audience fix is meant to enforce.
    assert captured.get("audience") == svc.keycloak_client_id
    assert captured.get("options", {}).get("verify_aud") is True


async def test_validate_token_audience_mismatch_rejected(monkeypatch, make_service):
    svc, _ = make_service()

    def fake_decode(*a, **k):
        raise auth_module.JWTClaimsError("Invalid audience")

    monkeypatch.setattr(auth_module.jwt, "decode", fake_decode)
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.validate_token("t")
    assert ei.value.status_code == 401
    assert "claim verification failed" in ei.value.detail


async def test_validate_token_missing_username(monkeypatch, make_service):
    svc, _ = make_service()
    monkeypatch.setattr(auth_module.jwt, "decode", lambda *a, **k: {"sub": "123"})
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.validate_token("t")
    assert ei.value.status_code == 401
    assert "missing username" in ei.value.detail


async def test_validate_token_decode_error(monkeypatch, make_service):
    svc, _ = make_service()

    def boom(*a, **k):
        raise Exception("bad sig")

    monkeypatch.setattr(auth_module.jwt, "decode", boom)
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.validate_token("t")
    assert ei.value.status_code == 401
    assert "bad sig" in ei.value.detail


async def test_exchange_token_success(make_service):
    svc, kc = make_service()
    kc._token = {"access_token": "x"}
    out = await svc.exchange_token("code", "http://r", "ver")
    assert out["access_token"] == "x"


async def test_exchange_token_failure(monkeypatch, make_service):
    svc, kc = make_service()
    kc.token = lambda **k: (_ for _ in ()).throw(Exception("oops"))
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.exchange_token("c", "u", "v")
    assert ei.value.status_code == 400


async def test_refresh_token_success(make_service):
    svc, kc = make_service()
    kc._token = {"access_token": "A1", "refresh_token": "R1", "expires_in": 123}
    kc._userinfo = {"preferred_username": "alice"}
    out = await svc.refresh_token("R0")
    assert out["access_token"] == "A1"
    assert out["refresh_token"] == "R1"
    assert out["expires_in"] == 123
    assert out["user_info"]["preferred_username"] == "alice"


async def test_refresh_token_failure(make_service):
    svc, kc = make_service()
    kc.refresh_token = lambda rt: (_ for _ in ()).throw(Exception("nope"))
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.refresh_token("R0")
    assert ei.value.status_code == 401


async def test_get_user_info_success(make_service):
    svc, kc = make_service()
    kc._userinfo = {"preferred_username": "z"}
    info = await svc.get_user_info("A")
    assert info["preferred_username"] == "z"


async def test_get_user_info_failure(make_service):
    svc, kc = make_service()
    kc.userinfo = lambda at: (_ for _ in ()).throw(Exception("x"))
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.get_user_info("A")
    assert ei.value.status_code == 401


async def test_get_admin_token_caches(monkeypatch, make_service):
    svc, _ = make_service()
    calls = {"count": 0}

    def fake_post(url, **kwargs):
        calls["count"] += 1
        return Resp(200, {"access_token": f"adm{calls['count']}", "expires_in": 60})

    fake_client = FakeAsyncClient({"post": fake_post})
    _patch_http_client(monkeypatch, svc, fake_client)

    t1 = await svc._get_admin_token()
    t2 = await svc._get_admin_token()
    assert t1 == "adm1" and t2 == "adm1"
    assert calls["count"] == 1  # cached on second call


async def test_update_user_profile_success(monkeypatch, make_service):
    svc, _ = make_service()
    captured = {}

    async def fake_admin_token(client=None):
        return "ADM"

    monkeypatch.setattr(svc, "_get_admin_token", fake_admin_token)

    def fake_put(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return Resp(204, {})

    fake_client = FakeAsyncClient({"put": fake_put})
    _patch_http_client(monkeypatch, svc, fake_client)

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


async def test_update_user_profile_timeout(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_admin_token(client=None):
        return "ADM"

    monkeypatch.setattr(svc, "_get_admin_token", fake_admin_token)

    def fake_put(*a, **k):
        raise httpx.TimeoutException("timeout")

    fake_client = FakeAsyncClient({"put": fake_put})
    _patch_http_client(monkeypatch, svc, fake_client)

    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.update_user_profile("user-1", {})
    assert ei.value.status_code == 408


async def test_update_user_profile_request_exception(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_admin_token(client=None):
        return "ADM"

    monkeypatch.setattr(svc, "_get_admin_token", fake_admin_token)

    def fake_put(*a, **k):
        raise httpx.HTTPError("bad")

    fake_client = FakeAsyncClient({"put": fake_put})
    _patch_http_client(monkeypatch, svc, fake_client)

    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.update_user_profile("user-1", {})
    assert ei.value.status_code == 400


async def test_logout_success(make_service):
    svc, kc = make_service()
    await svc.logout("R")
    assert kc.logout_called_with == "R"


async def test_logout_failure(make_service):
    svc, kc = make_service()
    kc.logout = lambda refresh_token: (_ for _ in ()).throw(Exception("boom"))
    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.logout("R")
    assert ei.value.status_code == 400


async def test_health_check(make_service):
    svc, kc = make_service()
    assert await svc.health_check() is True
    kc.well_known = lambda: (_ for _ in ()).throw(Exception("down"))
    assert await svc.health_check() is False


async def test_get_client_id_success(make_service):
    svc, _ = make_service()

    def fake_get(url, **kwargs):
        assert kwargs.get("params") == {"clientId": "spoutbreezeAPI"}
        return Resp(200, [{"id": "cid-1"}])

    fake_client = FakeAsyncClient({"get": fake_get})
    out = await svc._get_client_id(fake_client, "ADM", "spoutbreezeAPI")
    assert out == "cid-1"


async def test_get_client_id_not_found(make_service):
    svc, _ = make_service()

    def fake_get(url, **kwargs):
        return Resp(200, [])

    fake_client = FakeAsyncClient({"get": fake_get})
    with pytest.raises(ValueError):
        await svc._get_client_id(fake_client, "ADM", "missing")


async def test_get_client_role_success(make_service):
    svc, _ = make_service()

    def fake_get(url, **kwargs):
        return Resp(200, {"id": "rid", "name": "roleA"})

    fake_client = FakeAsyncClient({"get": fake_get})
    out = await svc._get_client_role(fake_client, "ADM", "cid", "roleA")
    assert out["name"] == "roleA"


async def test_get_user_client_roles_success(make_service):
    svc, _ = make_service()

    def fake_get(url, **kwargs):
        return Resp(200, [{"name": "oldRole"}])

    fake_client = FakeAsyncClient({"get": fake_get})
    out = await svc._get_user_client_roles(fake_client, "ADM", "uid", "cid")
    assert out and out[0]["name"] == "oldRole"


async def test_get_user_client_roles_exception_returns_empty(make_service):
    svc, _ = make_service()

    def fake_get(*a, **k):
        raise httpx.HTTPError("net")

    fake_client = FakeAsyncClient({"get": fake_get})
    out = await svc._get_user_client_roles(fake_client, "ADM", "uid", "cid")
    assert out == []


async def test_remove_user_client_roles_noop(make_service):
    svc, _ = make_service()
    called = {"del": False}

    def fake_delete(*a, **k):
        called["del"] = True
        return Resp(204)

    fake_client = FakeAsyncClient({"delete": fake_delete})
    await svc._remove_user_client_roles(fake_client, "ADM", "uid", "cid", [])
    assert called["del"] is False


async def test_remove_user_client_roles_success(make_service):
    svc, _ = make_service()
    called = {"json": None}

    def fake_delete(url, **kwargs):
        called["json"] = kwargs.get("json")
        return Resp(204)

    fake_client = FakeAsyncClient({"delete": fake_delete})
    await svc._remove_user_client_roles(fake_client, "ADM", "uid", "cid", [{"name": "old"}])
    assert called["json"] == [{"name": "old"}]


async def test_assign_user_client_role_success(make_service):
    svc, _ = make_service()
    captured = {"json": None}

    def fake_post(url, **kwargs):
        captured["json"] = kwargs.get("json")
        return Resp(204)

    fake_client = FakeAsyncClient({"post": fake_post})
    await svc._assign_user_client_role(fake_client, "ADM", "uid", "cid", {"name": "roleX"})
    assert captured["json"] == [{"name": "roleX"}]


async def test_update_user_role_happy_path(monkeypatch, make_service):
    svc, _ = make_service()
    called = {"removed": None, "assigned": None}

    async def fake_admin_token(client=None):
        return "ADM"

    async def fake_get_client_id(client, adm, name):
        return "cid-1"

    async def fake_get_user_client_roles(client, adm, uid, cid):
        return [{"name": "old"}]

    async def fake_remove(client, adm, uid, cid, roles):
        called["removed"] = roles

    async def fake_get_client_role(client, adm, cid, role):
        return {"id": "rid", "name": role}

    async def fake_assign(client, adm, uid, cid, role):
        called["assigned"] = role["name"]

    monkeypatch.setattr(svc, "_get_admin_token", fake_admin_token)
    monkeypatch.setattr(svc, "_get_client_id", fake_get_client_id)
    monkeypatch.setattr(svc, "_get_user_client_roles", fake_get_user_client_roles)
    monkeypatch.setattr(svc, "_remove_user_client_roles", fake_remove)
    monkeypatch.setattr(svc, "_get_client_role", fake_get_client_role)
    monkeypatch.setattr(svc, "_assign_user_client_role", fake_assign)

    # Stub the http client so the `async with` in update_user_role doesn't
    # touch the network during the test.
    _patch_http_client(monkeypatch, svc, FakeAsyncClient())

    await svc.update_user_role("uid-1", "admin")
    assert called["removed"] == [{"name": "old"}]
    assert called["assigned"] == "admin"


async def test_update_user_role_failure_wrapped(monkeypatch, make_service):
    svc, _ = make_service()

    async def boom(client=None):
        raise Exception("fail")

    monkeypatch.setattr(svc, "_get_admin_token", boom)
    _patch_http_client(monkeypatch, svc, FakeAsyncClient())

    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.update_user_role("uid", "role")
    assert ei.value.status_code == 500


async def test_delete_user_success(monkeypatch, make_service):
    svc, _ = make_service()
    captured = {}

    async def fake_admin_token(client=None):
        return "ADM"

    monkeypatch.setattr(svc, "_get_admin_token", fake_admin_token)

    def fake_delete(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return Resp(204)

    fake_client = FakeAsyncClient({"delete": fake_delete})
    _patch_http_client(monkeypatch, svc, fake_client)

    ok = await svc.delete_user("user-1")
    assert ok is True
    assert "user-1" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer ADM"


async def test_delete_user_timeout(monkeypatch, make_service):
    svc, _ = make_service()

    async def fake_admin_token(client=None):
        return "ADM"

    monkeypatch.setattr(svc, "_get_admin_token", fake_admin_token)

    def fake_delete(*a, **k):
        raise httpx.TimeoutException("timeout")

    fake_client = FakeAsyncClient({"delete": fake_delete})
    _patch_http_client(monkeypatch, svc, fake_client)

    with pytest.raises(auth_module.HTTPException) as ei:
        await svc.delete_user("user-1")
    assert ei.value.status_code == 408
