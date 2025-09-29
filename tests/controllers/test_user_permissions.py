import types
import uuid
import pytest

from app.main import app
from app.controllers import user_controller


class DummyUser:
    def __init__(self, username: str, roles=None):
        self.id = uuid.UUID("00000000-0000-0000-0000-000000000123")
        self.keycloak_id = "kc-123"
        self.username = username
        self.email = f"{username}@example.com"
        self.first_name = username.title()
        self.last_name = "User"
        self._roles = set(roles or [])

    # Methods used by role dependencies
    def has_role(self, role: str) -> bool:
        return role in self._roles

    def has_any_role(self, *roles: str) -> bool:
        return any(r in self._roles for r in roles)

    def get_roles_list(self):
        return list(self._roles)


@pytest.mark.anyio
async def test_users_forbidden_for_non_admin(client, monkeypatch):
    # Non-admin current user
    app.dependency_overrides[user_controller.get_current_user] = lambda: DummyUser(
        "viewer", roles=["viewer"]
    )
    try:
        # Ensure service is not even called when forbidden
        async def should_not_be_called(*a, **k):
            assert False, "Service should not be called for forbidden user"

        monkeypatch.setattr(
            user_controller.user_service_cached, "get_users_list_cached", should_not_be_called
        )

        resp = await client.get("/api/users")
        assert resp.status_code == 403
        assert "required to access this resource" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(user_controller.get_current_user, None)


@pytest.mark.anyio
async def test_users_allowed_for_admin(client, monkeypatch):
    app.dependency_overrides[user_controller.get_current_user] = lambda: DummyUser(
        "admin", roles=["admin"]
    )
    try:
        # Return minimal, schema-like user dict
        async def fake_get_users(skip, limit, db):
            return [
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "username": "alice",
                    "email": "alice@example.com",
                    "first_name": "Alice",
                    "last_name": "A",
                    "roles": "admin",
                    "is_active": True,
                    "keycloak_id": "kc-1",
                }
            ]

        monkeypatch.setattr(
            user_controller.user_service_cached, "get_users_list_cached", fake_get_users
        )

        resp = await client.get("/api/users")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list) and len(data) == 1
        assert data[0]["username"] == "alice"
    finally:
        app.dependency_overrides.pop(user_controller.get_current_user, None)


@pytest.mark.anyio
async def test_cache_stats_forbidden_for_non_admin(client):
    app.dependency_overrides[user_controller.get_current_user] = lambda: DummyUser(
        "viewer", roles=["viewer"]
    )
    try:
        resp = await client.get("/api/cache/stats")
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(user_controller.get_current_user, None)


@pytest.mark.anyio
async def test_cache_stats_allowed_for_admin(client, monkeypatch):
    app.dependency_overrides[user_controller.get_current_user] = lambda: DummyUser(
        "admin", roles=["admin"]
    )
    try:
        async def ok_health():
            return True

        # Make health check succeed; leave redis_client as is
        monkeypatch.setattr(user_controller.cache, "health_check", ok_health)

        resp = await client.get("/api/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cache_status"] in ("healthy", "unhealthy")
        # If we mocked healthy:
        assert data["cache_status"] == "healthy"
        assert isinstance(data["cache_patterns"], list) and len(data["cache_patterns"]) > 0
    finally:
        app.dependency_overrides.pop(user_controller.get_current_user, None)