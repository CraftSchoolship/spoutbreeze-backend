from typing import Optional
from app.config.redis_config import cache


def _key_meeting_to_user(meeting_id: str) -> str:
    return f"chat:meeting:{meeting_id}:user_id"


async def set_user_mapping(meeting_id: str, user_id: str, ttl: int = 86400) -> None:
    await cache.connect()
    await cache.set(_key_meeting_to_user(meeting_id), user_id, ttl)


async def get_user_mapping(meeting_id: str) -> Optional[str]:
    await cache.connect()
    return await cache.get(_key_meeting_to_user(meeting_id))


async def delete_user_mapping(meeting_id: str) -> None:
    await cache.connect()
    await cache.delete(_key_meeting_to_user(meeting_id))
