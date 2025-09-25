from __future__ import annotations
import pickle
import hashlib
from typing import Optional, Callable, Any, TypeVar, ParamSpec, cast, Coroutine
from functools import wraps

import redis.asyncio as redis
from redis.asyncio import Redis

from app.config.settings import get_settings
from app.config.logger_config import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()
logger = get_logger("Redis")


class RedisCache:
    def __init__(self) -> None:
        self.redis_client: Optional[Redis] = None

    async def connect(self) -> None:
        if self.redis_client:
            return
        try:
            self.redis_client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=False,
                max_connections=20,
                retry_on_timeout=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
            await self.redis_client.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.error(f"Redis connect failed: {e}")
            self.redis_client = None

    async def close(self) -> None:
        if self.redis_client:
            try:
                await self.redis_client.close()
            except Exception as e:
                logger.error(f"Redis close error: {e}")

    async def get(self, key: str) -> Optional[Any]:
        if not self.redis_client:
            return None
        try:
            raw = await self.redis_client.get(key)
            if raw is None:
                return None
            return pickle.loads(raw)
        except Exception as e:
            logger.error(f"GET {key} error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        if not self.redis_client:
            return False
        try:
            await self.redis_client.setex(key, ttl, pickle.dumps(value))
            logger.info(f"Cache SET for key: {key}")
            return True
        except Exception as e:
            logger.error(f"SET {key} error: {e}")
            return False

    async def delete(self, key: str) -> bool:
        if not self.redis_client:
            return False
        try:
            await self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.error(f"DEL {key} error: {e}")
            return False

    async def delete_pattern(self, pattern: str) -> bool:
        if not self.redis_client:
            return False
        try:
            keys = await self.redis_client.keys(pattern)
            if keys:
                await self.redis_client.delete(*keys)
            return True
        except Exception as e:
            logger.error(f"DEL pattern {pattern} error: {e}")
            return False

    async def health_check(self) -> bool:
        if not self.redis_client:
            return False
        try:
            await self.redis_client.ping()
            return True
        except Exception:
            return False


cache: RedisCache = RedisCache()


def generate_cache_key(*args: Any, **kwargs: Any) -> str:
    key_data = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(key_data.encode()).hexdigest()


P = ParamSpec("P")
R = TypeVar("R")


def cached(
    ttl: int = 300, key_prefix: str = ""
) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    # Generic decorator (keeps all args) - NOT ideal for DB session
    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            k: str = (
                f"{key_prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"
            )
            hit: Optional[R] = cast(Optional[R], await cache.get(k))
            if hit is not None:
                logger.info(f"Cache HIT for key: {k}")
                return hit
            result: R = await func(*args, **kwargs)
            await cache.set(k, result, ttl)
            return result

        return wrapper

    return decorator


def cached_db(
    ttl: int = 300, key_prefix: str = ""
) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    # Excludes AsyncSession objects from cache key
    def decorator(func: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            filt_args = [a for a in args if not isinstance(a, AsyncSession)]
            filt_kwargs = {
                k: v for k, v in kwargs.items() if not isinstance(v, AsyncSession)
            }
            k: str = f"{key_prefix}:{func.__name__}:{generate_cache_key(*filt_args, **filt_kwargs)}"
            hit: Optional[R] = cast(Optional[R], await cache.get(k))
            if hit is not None:
                logger.info(f"Cache HIT for key: {k}")
                return hit
            result: R = await func(*args, **kwargs)
            await cache.set(k, result, ttl)
            return result

        return wrapper

    return decorator
