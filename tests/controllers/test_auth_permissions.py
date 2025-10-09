import types
import pytest
from fastapi import HTTPException

from app.main import app
from app.controllers.user_controller import get_current_user


@pytest.mark.anyio
async def test_protected_requires_auth(client):
    def unauth_dep():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_current_user] = unauth_dep
    try:
        resp = await client.get("/api/protected")
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Not authenticated"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_protected_with_user(client):
    def user_dep():
        return types.SimpleNamespace(username="alice", roles=["viewer"])

    app.dependency_overrides[get_current_user] = user_dep
    try:
        resp = await client.get("/api/protected")
        assert resp.status_code == 200
        assert resp.json()["message"].startswith("Hello, alice!")
    finally:
        app.dependency_overrides.pop(get_current_user, None)