import pytest
from app.config import redis_config


class FailingCache:
    async def get(self, key): raise RuntimeError("down")
    async def set(self, key, value, ex=None): raise RuntimeError("down")
    async def delete_pattern(self, pattern): raise RuntimeError("down")
    async def health_check(self): raise RuntimeError("down")


@pytest.mark.anyio
async def test_cached_db_continues_when_cache_unavailable(monkeypatch, db_session):
    failing = FailingCache()
    monkeypatch.setattr(redis_config, "cache", failing)

    calls = {"n": 0}

    @redis_config.cached_db(ttl=30, key_prefix="no_redis")
    async def sample(x: int, db):
        calls["n"] += 1
        return x * 7

    r1 = await sample(3, db_session)
    r2 = await sample(3, db_session)
    # Both calls executed (no caching)
    assert r1 == 21 and r2 == 21 and calls["n"] == 2