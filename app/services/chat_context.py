from typing import Optional, List
from app.config.redis_config import cache


def _key_meeting_to_user(meeting_id: str) -> str:
    return f"chat:meeting:{meeting_id}:user_id"


def _key_user_streams_set(user_id: str) -> str:
    """Redis set containing all active stream_ids for a user"""
    return f"streams:user:{user_id}:active"


def _key_stream_to_user(stream_id: str) -> str:
    """Map stream_id back to user_id"""
    return f"streams:stream:{stream_id}:user_id"


async def set_user_mapping(meeting_id: str, user_id: str, ttl: int = 86400) -> None:
    await cache.connect()
    await cache.set(_key_meeting_to_user(meeting_id), user_id, ttl)


async def get_user_mapping(meeting_id: str) -> Optional[str]:
    await cache.connect()
    return await cache.get(_key_meeting_to_user(meeting_id))


async def delete_user_mapping(meeting_id: str) -> None:
    await cache.connect()
    await cache.delete(_key_meeting_to_user(meeting_id))


# Stream tracking functions
async def add_user_stream(user_id: str, stream_id: str, ttl: int = 86400) -> None:
    """Add a stream to user's active streams set"""
    await cache.connect()
    # Add stream to user's set
    await cache.sadd(_key_user_streams_set(user_id), stream_id)
    # Set expiry on the set
    await cache.expire(_key_user_streams_set(user_id), ttl)
    # Map stream back to user for cleanup
    await cache.set(_key_stream_to_user(stream_id), user_id, ttl)


async def remove_user_stream(stream_id: str) -> None:
    """Remove a stream from user's active streams"""
    await cache.connect()
    # Get user_id for this stream
    user_id = await cache.get(_key_stream_to_user(stream_id))
    if user_id:
        # Remove from user's set
        await cache.srem(_key_user_streams_set(user_id), stream_id)
    # Delete stream → user mapping
    await cache.delete(_key_stream_to_user(stream_id))


async def get_user_streams(user_id: str) -> List[str]:
    """Get all active stream_ids for a user"""
    await cache.connect()
    streams = await cache.smembers(_key_user_streams_set(user_id))
    return list(streams) if streams else []


async def get_user_stream_count(user_id: str) -> int:
    """Get count of active streams for a user"""
    await cache.connect()
    return await cache.scard(_key_user_streams_set(user_id)) or 0
