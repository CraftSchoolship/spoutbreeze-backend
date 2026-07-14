"""Tests for the public landing-page contact endpoint (N14).

`POST /api/contact` is unauthenticated and dispatches an SMTP email per call,
so it must be rate-limited to prevent inbox-flooding / SMTP-budget abuse. It
also relays user-supplied text, so the happy/failure/validation paths are
pinned here too.
"""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.anyio


_VALID_PAYLOAD = {
    "name": "Ada Lovelace",
    "email": "ada@example.com",
    "message": "I'd love to learn more about BlueScale for our university.",
}


async def test_contact_happy_path_relays_email(client):
    with patch(
        "app.controllers.contact_controller.send_email",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        resp = await client.post("/api/contact", json=_VALID_PAYLOAD)

    assert resp.status_code == 200
    assert resp.json() == {"status": "sent"}
    mock_send.assert_awaited_once()


async def test_contact_returns_502_when_send_fails(client):
    with patch(
        "app.controllers.contact_controller.send_email",
        new=AsyncMock(return_value=False),
    ):
        resp = await client.post("/api/contact", json=_VALID_PAYLOAD)

    assert resp.status_code == 502


async def test_contact_rejects_short_message(client):
    payload = {**_VALID_PAYLOAD, "message": "too short"}
    resp = await client.post("/api/contact", json=payload)
    assert resp.status_code == 422


async def test_contact_is_rate_limited(client):
    """rate_limit_contact defaults to 3/minute; the 4th call from the same IP
    must be rejected with 429 before the handler emails anything."""
    with patch(
        "app.controllers.contact_controller.send_email",
        new=AsyncMock(return_value=True),
    ):
        codes = [(await client.post("/api/contact", json=_VALID_PAYLOAD)).status_code for _ in range(4)]

    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429
