"""Regression tests for the log-hygiene cleanup of `/api/token` and `/api/dev-token`.

The original issues:
- `auth_controller.py` `print()`d the full Keycloak `user_info` payload to
  stdout on every login. That payload includes email, given/family name —
  PII that ended up in pod logs and any aggregator they piped to.
- `/api/dev-token` accepted `username` and `password` as query parameters,
  so credentials landed in access logs, browser history, and any caching
  proxy in the path.
"""

from pathlib import Path

import pytest

import app.controllers.auth_controller as auth_controller

REPO_SRC = Path(auth_controller.__file__).parent.parent  # app/


def test_no_bare_print_in_app_code():
    """A `print(` anywhere under app/ is the symptom that previously
    leaked the full Keycloak user_info to stdout. Stay vigilant."""
    offenders = []
    for path in REPO_SRC.rglob("*.py"):
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            stripped = raw.lstrip()
            if stripped.startswith("print(") and "# noqa" not in raw:
                offenders.append(f"{path.relative_to(REPO_SRC.parent)}:{lineno}: {raw.strip()}")
    assert not offenders, "Unexpected print() calls:\n" + "\n".join(offenders)


@pytest.mark.anyio
async def test_dev_token_rejects_query_param_credentials(client, monkeypatch):
    """The endpoint must require credentials in the JSON body, not the
    query string — query-string creds end up in access logs."""
    monkeypatch.setattr(auth_controller.settings, "env", "development")

    # Old-style call: credentials as query params, empty body.
    resp = await client.post(
        "/api/dev-token",
        params={"username": "alice", "password": "hunter2"},
    )
    # FastAPI returns 422 when the required JSON body is missing.
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_dev_token_accepts_body_credentials(client, monkeypatch):
    """Body-supplied credentials reach the Keycloak call (which we stub
    out — the test just verifies the wiring, not the IDP)."""
    monkeypatch.setattr(auth_controller.settings, "env", "development")

    captured: dict = {}

    def fake_token(**kwargs):
        captured.update(kwargs)
        return {
            "access_token": "A",
            "refresh_token": "R",
            "expires_in": 300,
            "refresh_expires_in": 3600,
        }

    async def fake_get_user_info(at):
        return {
            "sub": "kc-1",
            "preferred_username": "alice",
            "given_name": "Alice",
            "family_name": "Doe",
            "email": "alice@example.com",
        }

    # Swap the Keycloak factory so the endpoint reaches our fake without
    # instantiating a real python-keycloak client.
    class _StubKC:
        token = staticmethod(fake_token)

    monkeypatch.setattr(auth_controller, "get_keycloak_openid", lambda: _StubKC)
    monkeypatch.setattr(auth_controller.auth_service, "get_user_info", fake_get_user_info)

    resp = await client.post(
        "/api/dev-token",
        json={"username": "alice", "password": "hunter2"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["username"] == "alice"
    assert captured["password"] == "hunter2"


@pytest.mark.anyio
async def test_dev_token_rejected_outside_development(client, monkeypatch):
    """Belt-and-braces: even with a valid body, the endpoint 404s in
    non-development environments."""
    monkeypatch.setattr(auth_controller.settings, "env", "production")
    resp = await client.post(
        "/api/dev-token",
        json={"username": "alice", "password": "hunter2"},
    )
    assert resp.status_code == 404
