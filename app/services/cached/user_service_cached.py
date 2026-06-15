from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logger_config import get_logger
from app.config.redis_config import cache, cached_db
from app.config.settings import get_settings
from app.models.user_models import User

logger = get_logger("UserServiceCached")
settings = get_settings()


class UserServiceCached:
    """Cached user service for optimized user operations"""

    @cached_db(ttl=settings.cache_ttl_user, key_prefix="user_profile")  # 15 minutes
    async def get_user_by_id_cached(self, user_id: UUID, db: AsyncSession) -> User | None:
        """Get user by ID with caching"""
        stmt = select(User).where(User.id == user_id)
        res = await db.execute(stmt)
        return res.scalars().first()

    @cached_db(ttl=settings.cache_ttl_user, key_prefix="user_firebase")  # 15 minutes
    async def get_user_by_firebase_uid_cached(self, firebase_uid: str, db: AsyncSession) -> User | None:
        """Get user by Firebase UID with caching"""
        stmt = select(User).where(User.firebase_uid == firebase_uid)
        res = await db.execute(stmt)
        return res.scalars().first()

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="user_roles")  # 30 minutes - roles change less frequently
    async def get_user_roles_cached(self, user_id: UUID, db: AsyncSession) -> list[str]:
        """Get user roles with caching"""
        u = await self.get_user_by_id_cached(user_id, db)
        return u.get_roles_list() if u else []

    @cached_db(ttl=settings.cache_ttl_long, key_prefix="users_list")  # 30 minutes for admin lists
    async def get_users_list_cached(self, skip: int, limit: int, db: AsyncSession) -> list[User]:
        """Get users list with caching (for admin endpoints)"""
        stmt = select(User).offset(skip).limit(limit)
        res = await db.execute(stmt)
        return list(res.scalars().all())  # Convert to list for serialization

    async def invalidate_user_cache(self, user_id: UUID, firebase_uid: str | None = None):
        """Invalidate all caches for a specific user.

        ``cached_db`` builds keys as ``{prefix}:{func_name}:{md5(args)}`` —
        the user_id / firebase_uid never appears literally in the key, so a
        substring pattern like ``user_profile:*<user_id>*`` matches nothing.
        Dropping the whole prefix is the only correct option. With a small
        user count this is fine; even at scale the 15-min TTL bounds the
        cost of re-warming.
        """
        await cache.delete_pattern("user_profile:*")
        await cache.delete_pattern("user_roles:*")
        if firebase_uid:
            await cache.delete_pattern("user_firebase:*")
        await cache.delete_pattern("users_list:*")
        logger.info(f"Invalidated user caches for {user_id}")

    async def update_user_profile(self, user_id: UUID, updates: dict, db: AsyncSession):
        """Update user profile and invalidate cache"""
        stmt = update(User).where(User.id == user_id).values(**updates)
        await db.execute(stmt)
        await db.commit()
        user = await self.get_user_by_id_cached(user_id, db)
        await self.invalidate_user_cache(user_id, user.firebase_uid if user else None)
        return user

    async def update_user_role(self, user_id: UUID, new_role: str, db: AsyncSession):
        """Update user role and invalidate cache"""
        stmt = update(User).where(User.id == user_id).values(roles=new_role)
        await db.execute(stmt)
        await db.commit()
        user = await self.get_user_by_id_cached(user_id, db)
        await self.invalidate_user_cache(user_id, user.firebase_uid if user else None)
        return user


# Global cached user service instance
user_service_cached = UserServiceCached()
