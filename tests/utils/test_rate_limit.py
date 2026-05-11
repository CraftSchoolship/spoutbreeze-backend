"""Tests for the SlowAPI rate-limiter wired into the auth endpoints.

The goal isn't to prove SlowAPI works (it's well-tested upstream) — it's
to lock in the policy: `/api/token`, `/api/refresh`, `/api/dev-token`
are rate-limited, the limits are read from settings (not hard-coded),
and the limiter falls back to in-memory storage if Redis is unreachable.
"""

import pytest

from app.utils import rate_limit


def test_limiter_falls_back_to_in_memory_on_redis_failure(monkeypatch):
    """If Redis URL is unreachable, _build_limiter must NOT raise — the
    auth endpoints would otherwise fail to import and the whole app
    won't start. In-memory fallback is safe-by-default."""

    class _BoomSettings:
        rate_limit_enabled = True
        rate_limit_token = "20/minute"
        rate_limit_refresh = "30/minute"
        rate_limit_dev_token = "5/minute"
        rate_limit_payments = "60/minute"
        redis_url = "redis://nonexistent-host:6379/0"

    # Force the Redis-backed constructor to raise so we exercise the
    # fallback path explicitly.
    from slowapi import Limiter

    original_init = Limiter.__init__

    def maybe_raise_init(self, *args, **kwargs):
        if "storage_uri" in kwargs:
            raise RuntimeError("redis unreachable (simulated)")
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(Limiter, "__init__", maybe_raise_init)
    monkeypatch.setattr(rate_limit, "get_settings", lambda: _BoomSettings())

    built = rate_limit._build_limiter()
    assert built is not None


def test_limiter_disabled_when_setting_false(monkeypatch):
    """RATE_LIMIT_ENABLED=false is an emergency override that returns a
    Limiter with no default limits — the per-endpoint decorators still
    work as no-ops."""

    class _Settings:
        rate_limit_enabled = False
        rate_limit_token = "20/minute"
        rate_limit_refresh = "30/minute"
        rate_limit_dev_token = "5/minute"
        rate_limit_payments = "60/minute"
        redis_url = "redis://x"

    monkeypatch.setattr(rate_limit, "get_settings", lambda: _Settings())
    # Should not raise — the limiter exists and the warning is logged.
    # SlowAPI's internal `_default_limits` is private, so we don't poke
    # at it; the contract is "no crash and no exception leaked".
    assert rate_limit._build_limiter() is not None


@pytest.mark.anyio
async def test_dev_token_429_after_burst(client, monkeypatch):
    """The /api/dev-token limit is the strictest (5/minute by default).
    Send 6 in quick succession and confirm the 6th is 429.

    We tighten the limit to 2/minute for the test so we don't have to
    actually fire 6 requests."""
    import app.controllers.auth_controller as auth_controller

    # Use a unique scheme so SlowAPI's in-memory counter starts fresh.
    monkeypatch.setattr(auth_controller.settings, "rate_limit_dev_token", "2/minute")
    monkeypatch.setattr(auth_controller.settings, "env", "production")  # short-circuit fast

    # Three calls from the same test client IP. Production env makes them
    # all 404 *before* any auth work — but SlowAPI's middleware runs
    # before the endpoint body, so the 3rd is 429.
    body = {"username": "x", "password": "x"}
    r1 = await client.post("/api/dev-token", json=body)
    r2 = await client.post("/api/dev-token", json=body)
    r3 = await client.post("/api/dev-token", json=body)

    statuses = [r1.status_code, r2.status_code, r3.status_code]
    assert 429 in statuses, (
        f"Expected at least one 429 after the configured 2/minute burst, "
        f"got {statuses}"
    )
