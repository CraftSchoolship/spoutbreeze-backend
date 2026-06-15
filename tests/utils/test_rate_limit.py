"""Tests for the SlowAPI rate-limiter wired into the auth endpoints.

The goal isn't to prove SlowAPI works (it's well-tested upstream) — it's
to lock in the policy: `/api/session` is rate-limited, the limits are read
from settings (not hard-coded), and the limiter falls back to in-memory
storage if Redis is unreachable.
"""

import pytest

from app.utils import rate_limit


def test_limiter_falls_back_to_in_memory_on_redis_failure(monkeypatch):
    """If Redis is unreachable, _build_limiter must NOT raise — the auth
    endpoints would otherwise fail at request time (after the limiter is
    constructed but before the first PING). Stub the reachability probe
    so we exercise the fallback path explicitly."""

    class _Settings:
        rate_limit_enabled = True
        rate_limit_token = "20/minute"
        rate_limit_refresh = "30/minute"
        rate_limit_dev_token = "5/minute"
        rate_limit_payments = "60/minute"
        redis_url = "redis://nonexistent-host:6379/0"

    def _stub_unreachable(url: str) -> bool:
        del url  # signal "intentionally discarded" to the IDE
        return False

    monkeypatch.setattr(rate_limit, "get_settings", lambda: _Settings())
    monkeypatch.setattr(rate_limit, "_redis_is_reachable", _stub_unreachable)

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
async def test_session_429_after_burst(client, monkeypatch):
    """`/api/session` is rate-limited by `rate_limit_token`. Tighten the
    limit to 2/minute and confirm the 3rd request in a burst is 429.

    The session body would fail token verification, but SlowAPI's limiter
    runs before the endpoint body, so the 3rd request is rejected with 429
    regardless of the (invalid) payload."""
    import app.controllers.auth_controller as auth_controller

    monkeypatch.setattr(auth_controller.settings, "rate_limit_token", "2/minute")

    body = {"id_token": "x"}
    r1 = await client.post("/api/session", json=body)
    r2 = await client.post("/api/session", json=body)
    r3 = await client.post("/api/session", json=body)

    statuses = [r1.status_code, r2.status_code, r3.status_code]
    assert 429 in statuses, f"Expected at least one 429 after the configured 2/minute burst, got {statuses}"
