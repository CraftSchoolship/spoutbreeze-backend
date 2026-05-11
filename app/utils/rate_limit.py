"""Shared rate-limiter for auth and payment endpoints.

Backed by SlowAPI. Uses Redis as the storage backend when REDIS_URL is
reachable so a multi-worker / multi-pod deployment shares one counter
per IP — without that, an N-worker fleet effectively gets N× the
configured limit.

If Redis isn't reachable at import time, falls back to in-memory storage
so local dev and the test suite still work without a running Redis.

Settings (see app/config/settings.py):
  RATE_LIMIT_ENABLED     master switch (default true)
  RATE_LIMIT_TOKEN       e.g. "20/minute" — applied to /api/token
  RATE_LIMIT_REFRESH     e.g. "30/minute" — applied to /api/refresh
  RATE_LIMIT_DEV_TOKEN   e.g. "5/minute"  — applied to /api/dev-token
  RATE_LIMIT_PAYMENTS    e.g. "60/minute" — applied to sensitive
                                            /api/payments/* endpoints
"""

from __future__ import annotations

import redis
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.config.logger_config import get_logger
from app.config.settings import get_settings

logger = get_logger("RateLimit")


def _redis_is_reachable(url: str) -> bool:
    """Probe the configured Redis URL with a short-timeout PING.

    SlowAPI's `Limiter(storage_uri=...)` constructor only stores the URL
    — the connection isn't opened until the first rate-limit check. By
    then we're past any try/except wrapping construction and a Redis
    outage 500s the request instead of falling back. Probe up front so
    the choice between Redis-backed and in-memory storage is made when
    the app is starting, not on the hot path.
    """
    try:
        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
        client.ping()
        client.close()
        return True
    except Exception as e:
        logger.warning(
            f"Rate-limit Redis backend unreachable at {url} ({e!r}); "
            "falling back to in-memory storage. Multi-worker deployments "
            "will see per-worker counters instead of a shared limit — fix "
            "Redis connectivity to restore correct behavior."
        )
        return False


def _build_limiter() -> Limiter:
    """Construct the shared Limiter.

    Tries Redis first (so multiple workers share state); falls back to
    SlowAPI's default in-memory storage if Redis can't be PINGed at
    startup. The fallback is safe-by-default — a single misconfigured
    pod can't accidentally bypass rate-limiting by failing to reach Redis.
    """
    settings = get_settings()

    if not settings.rate_limit_enabled:
        # Even when "disabled", we still return a Limiter so the
        # decorators don't error; just give it a permissive default.
        # Disabling via setting is meant for emergency overrides, not
        # the normal path.
        logger.warning(
            "RATE_LIMIT_ENABLED=false — auth endpoints have NO rate limit. "
            "Only use this for emergency response to a limiter bug."
        )
        return Limiter(key_func=get_remote_address, default_limits=[])

    if _redis_is_reachable(settings.redis_url):
        return Limiter(
            key_func=get_remote_address,
            storage_uri=settings.redis_url,
            strategy="fixed-window",
        )
    return Limiter(key_func=get_remote_address, strategy="fixed-window")


limiter: Limiter = _build_limiter()
