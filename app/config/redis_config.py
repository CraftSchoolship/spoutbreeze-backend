from __future__ import annotations

import builtins
import hashlib
import importlib
import json
from collections.abc import Callable, Coroutine
from datetime import date, datetime
from enum import Enum
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast
from uuid import UUID

import redis.asyncio as redis
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logger_config import get_logger
from app.config.settings import get_settings

settings = get_settings()
logger = get_logger("Redis")

# Type tags used in JSON-safe cache representations.
_TYPE_TAG = "__t"
_VALUE_TAG = "__v"

# Only classes from these top-level packages may be reconstructed from the
# cache. This blocks an attacker who can write to Redis from instantiating
# arbitrary classes (e.g. os.system, subprocess.Popen) on read — the threat
# that originally made `pickle.loads` unsafe.
_ALLOWED_DESERIALIZE_PREFIXES: tuple[str, ...] = ("app.",)


def _to_json_safe(obj: Any) -> Any:
    """Recursively convert obj to a JSON-safe representation with type tags."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_to_json_safe(x) for x in obj]
    if isinstance(obj, (set, frozenset)):
        return {_TYPE_TAG: "set", _VALUE_TAG: [_to_json_safe(x) for x in obj]}
    if isinstance(obj, dict):
        return {k: _to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, datetime):
        return {_TYPE_TAG: "datetime", _VALUE_TAG: obj.isoformat()}
    if isinstance(obj, date):
        return {_TYPE_TAG: "date", _VALUE_TAG: obj.isoformat()}
    if isinstance(obj, UUID):
        return {_TYPE_TAG: "uuid", _VALUE_TAG: str(obj)}
    if isinstance(obj, bytes):
        return {_TYPE_TAG: "bytes", _VALUE_TAG: obj.hex()}
    if isinstance(obj, Enum):
        cls = obj.__class__
        return {
            _TYPE_TAG: "enum",
            "module": cls.__module__,
            "class": cls.__qualname__,
            _VALUE_TAG: obj.value,
        }
    if isinstance(obj, BaseModel):
        cls = obj.__class__
        return {
            _TYPE_TAG: "pydantic",
            "module": cls.__module__,
            "class": cls.__qualname__,
            _VALUE_TAG: obj.model_dump(mode="json"),
        }
    if hasattr(obj, "__table__"):  # SQLAlchemy ORM mapped class
        cls = obj.__class__
        data = {col.name: _to_json_safe(getattr(obj, col.name, None)) for col in obj.__table__.columns}
        return {
            _TYPE_TAG: "sqlalchemy",
            "module": cls.__module__,
            "class": cls.__qualname__,
            _VALUE_TAG: data,
        }
    raise TypeError(f"Object of type {type(obj).__name__} is not cache-serializable")


_class_cache: dict[tuple[str, str], type] = {}


def _resolve_class(module_name: str, class_name: str) -> type:
    """Resolve module.ClassName, restricted to the app.* namespace allowlist."""
    if not isinstance(module_name, str) or not module_name.startswith(_ALLOWED_DESERIALIZE_PREFIXES):
        raise ValueError(f"Refusing to deserialize class from disallowed module: {module_name!r}")
    if not isinstance(class_name, str):
        raise ValueError(f"Invalid class name: {class_name!r}")

    key = (module_name, class_name)
    if key in _class_cache:
        return _class_cache[key]
    module = importlib.import_module(module_name)
    obj: Any = module
    for part in class_name.split("."):
        obj = getattr(obj, part)
    if not isinstance(obj, type):
        raise ValueError(f"Resolved object {module_name}.{class_name} is not a class")
    _class_cache[key] = obj
    return obj


def _from_json_safe(obj: Any) -> Any:
    """Recursively reconstruct typed values from their JSON-safe representation."""
    if isinstance(obj, list):
        return [_from_json_safe(x) for x in obj]
    if isinstance(obj, dict):
        tag = obj.get(_TYPE_TAG)
        if tag is None:
            return {k: _from_json_safe(v) for k, v in obj.items()}
        value = obj.get(_VALUE_TAG)
        if tag == "datetime":
            return datetime.fromisoformat(value)
        if tag == "date":
            return date.fromisoformat(value)
        if tag == "uuid":
            return UUID(value)
        if tag == "bytes":
            return bytes.fromhex(value)
        if tag == "set":
            return {_from_json_safe(x) for x in value}
        if tag in {"enum", "pydantic", "sqlalchemy"}:
            cls = _resolve_class(obj["module"], obj["class"])
            decoded = _from_json_safe(value)
            if tag == "enum":
                return cls(decoded)
            if tag == "pydantic":
                return cls.model_validate(decoded)  # type: ignore[attr-defined]
            if tag == "sqlalchemy":
                # Build a transient ORM instance — `cls()` runs the
                # auto-generated declarative __init__ so SQLAlchemy's
                # _sa_instance_state is wired up, then we populate columns.
                # No session is attached, so relationships will not lazy-load;
                # callers only access scalar columns and methods that
                # depend on them.
                instance = cls()
                for k, v in decoded.items():
                    setattr(instance, k, v)
                return instance
        return obj
    return obj


def _serialize(value: Any) -> bytes:
    return json.dumps(_to_json_safe(value)).encode("utf-8")


def _deserialize(raw: bytes | str) -> Any:
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    return _from_json_safe(json.loads(text))


class RedisCache:
    def __init__(self) -> None:
        self.redis_client: Redis | None = None

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
            ping_result = await self.redis_client.ping()  # type: ignore[misc]
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

    async def get(self, key: str) -> Any | None:
        if not self.redis_client:
            return None
        try:
            raw = await self.redis_client.get(key)
            if raw is None:
                return None
            return _deserialize(raw)
        except Exception as e:
            logger.error(f"GET {key} error: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        if not self.redis_client:
            return False
        try:
            await self.redis_client.setex(key, ttl, _serialize(value))
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
            ping_result = await self.redis_client.ping()  # type: ignore[misc]
            return True
        except Exception:
            return False

    # Redis Set operations
    async def sadd(self, key: str, *values: str) -> int:
        """Add members to a set"""
        if not self.redis_client:
            return 0
        try:
            result = await self.redis_client.sadd(key, *values)  # type: ignore[misc]
            return cast(int, result)
        except Exception as e:
            logger.error(f"SADD {key} error: {e}")
            return 0

    async def srem(self, key: str, *values: str) -> int:
        """Remove members from a set"""
        if not self.redis_client:
            return 0
        try:
            result = await self.redis_client.srem(key, *values)  # type: ignore[misc]
            return cast(int, result)
        except Exception as e:
            logger.error(f"SREM {key} error: {e}")
            return 0

    async def smembers(self, key: str) -> builtins.set[str]:
        """Get all members of a set"""
        if not self.redis_client:
            return set()
        try:
            members = await self.redis_client.smembers(key)  # type: ignore[misc]
            # Decode bytes to strings if needed
            return {m.decode() if isinstance(m, bytes) else m for m in members}
        except Exception as e:
            logger.error(f"SMEMBERS {key} error: {e}")
            return set()

    async def scard(self, key: str) -> int:
        """Get the number of members in a set"""
        if not self.redis_client:
            return 0
        try:
            result = await self.redis_client.scard(key)  # type: ignore[misc]
            return cast(int, result)
        except Exception as e:
            logger.error(f"SCARD {key} error: {e}")
            return 0

    async def expire(self, key: str, ttl: int) -> bool:
        """Set expiry on a key"""
        if not self.redis_client:
            return False
        try:
            return await self.redis_client.expire(key, ttl)
        except Exception as e:
            logger.error(f"EXPIRE {key} error: {e}")
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
    def decorator(
        func: Callable[P, Coroutine[Any, Any, R]],
    ) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            k: str = f"{key_prefix}:{func.__name__}:{generate_cache_key(*args, **kwargs)}"
            hit: R | None = None
            try:
                hit = cast(R | None, await cache.get(k))
            except Exception as e:
                logger.error(f"Decorator GET error ({k}): {e}")
            if hit is not None:
                logger.info(f"Cache HIT for key: {k}")
                return hit
            result: R = await func(*args, **kwargs)
            try:
                await cache.set(k, result, ttl)
            except Exception as e:
                logger.error(f"Decorator SET error ({k}): {e}")
            return result

        return wrapper

    return decorator


def cached_db(
    ttl: int = 300, key_prefix: str = ""
) -> Callable[[Callable[P, Coroutine[Any, Any, R]]], Callable[P, Coroutine[Any, Any, R]]]:
    def decorator(
        func: Callable[P, Coroutine[Any, Any, R]],
    ) -> Callable[P, Coroutine[Any, Any, R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            filt_args = [a for a in args if not isinstance(a, AsyncSession)]
            filt_kwargs = {k: v for k, v in kwargs.items() if not isinstance(v, AsyncSession)}
            k: str = f"{key_prefix}:{func.__name__}:{generate_cache_key(*filt_args, **filt_kwargs)}"
            hit: R | None = None
            try:
                hit = cast(R | None, await cache.get(k))
            except Exception as e:
                logger.error(f"Decorator GET error ({k}): {e}")
            if hit is not None:
                logger.info(f"Cache HIT for key: {k}")
                return hit
            result: R = await func(*args, **kwargs)
            try:
                await cache.set(k, result, ttl)
            except Exception as e:
                logger.error(f"Decorator SET error ({k}): {e}")
            return result

        return wrapper

    return decorator
