"""
Unified Notification Service.

This is the **single entry-point** for creating and dispatching notifications
across SpoutBreeze.  Other services call ``notify()`` or ``notify_bulk()`` —
they never interact with delivery backends or WebSocket connections directly.

Delivery decision matrix
────────────────────────
  ┌─────────────────────┬────────────────────────────────────────────┐
  │ User online?        │ Action                                     │
  ├─────────────────────┼────────────────────────────────────────────┤
  │ YES                 │ Deliver via WebSocket, update counter in   │
  │                     │ real-time.  Optionally email/push if flags │
  │                     │ are set.                                   │
  ├─────────────────────┼────────────────────────────────────────────┤
  │ NO                  │ Store in DB.  Send email / push in         │
  │                     │ background if enabled.  User fetches       │
  │                     │ in-app on next HTTP reconnect.             │
  └─────────────────────┴────────────────────────────────────────────┘
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logger_config import get_logger
from app.config.notification_ws_manager import notification_ws_manager
from app.config.redis_config import cache
from app.models.notification_models import (
    DeliveryStatus,
    Notification,
    NotificationPreference,
    NotificationPriority,
    NotificationType,
)
from app.models.notification_schemas import (
    NotificationCreate,
    NotificationListResponse,
    NotificationPreferenceListResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationResponse,
    WSNotificationEvent,
)
from app.services.notification_delivery import email_backend, push_backend

logger = get_logger("NotificationService")

# Redis key template for unread counter cache
_UNREAD_COUNTER_KEY = "notifications:unread:{user_id}"
_UNREAD_COUNTER_TTL = 900  # 15 min

# Deduplication window (seconds)
_DEDUP_TTL = 300  # 5 min

# Rate-limit: max notifications per user per minute
_RATE_LIMIT_KEY = "notifications:rate:{user_id}"
_RATE_LIMIT_MAX = 60
_RATE_LIMIT_TTL = 60  # 1 minute window


class NotificationService:
    """Unified notification service — the only public interface for notifications."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def notify(
        self,
        db: AsyncSession,
        payload: NotificationCreate,
        user_email: str | None = None,
    ) -> NotificationResponse:
        """
        Create a single notification and dispatch it to the appropriate
        channels (WebSocket, email, push) based on flags and user presence.

        Args:
            db: Active database session.
            payload: Notification creation payload.
            user_email: Recipient email (needed for email/push delivery).

        Returns:
            The created ``NotificationResponse``.
        """
        # --- Deduplication ---
        if payload.idempotency_key:
            existing = await self._check_idempotency(db, payload.idempotency_key)
            if existing:
                logger.info(f"[Notify] Duplicate skipped: key={payload.idempotency_key}")
                return NotificationResponse.model_validate(existing)

        # --- Rate limiting ---
        if not await self._check_rate_limit(payload.user_id):
            logger.warning(f"[Notify] Rate limit hit for user {payload.user_id}")
            raise ValueError("Notification rate limit exceeded. Try again later.")

        # --- Resolve user preferences (merge with explicit flags) ---
        prefs = await self._get_preferences(db, payload.user_id, payload.notification_type.value)
        send_in_app = payload.send_in_app and prefs["in_app"]
        send_email = payload.send_email and prefs["email"]
        send_push = payload.send_push and prefs["push"]

        # --- Persist notification ---
        notification = Notification(
            user_id=payload.user_id,
            notification_type=payload.notification_type.value,
            title=payload.title,
            body=payload.body,
            data=payload.data,
            priority=payload.priority.value,
            send_in_app=send_in_app,
            send_email=send_email,
            send_push=send_push,
            in_app_status=DeliveryStatus.PENDING.value if send_in_app else DeliveryStatus.SKIPPED.value,
            email_status=DeliveryStatus.PENDING.value if send_email else DeliveryStatus.SKIPPED.value,
            push_status=DeliveryStatus.PENDING.value if send_push else DeliveryStatus.SKIPPED.value,
            idempotency_key=payload.idempotency_key,
        )
        db.add(notification)
        await db.flush()
        await db.refresh(notification)
        await db.commit()

        response = NotificationResponse.model_validate(notification)

        # --- Dispatch ---
        await self._dispatch(
            db=db,
            notification=notification,
            response=response,
            user_email=user_email,
        )

        return response

    async def notify_bulk(
        self,
        db: AsyncSession,
        user_ids: list[UUID],
        notification_type: NotificationType,
        title: str,
        body: str,
        data: str | None = None,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        send_in_app: bool = True,
        send_email: bool = False,
        send_push: bool = False,
    ) -> int:
        """
        Send the same notification to multiple users.
        Returns the number of notifications created.
        """
        count = 0
        for uid in user_ids:
            try:
                payload = NotificationCreate(
                    user_id=uid,
                    notification_type=notification_type,
                    title=title,
                    body=body,
                    data=data,
                    priority=priority,
                    send_in_app=send_in_app,
                    send_email=send_email,
                    send_push=send_push,
                )
                await self.notify(db, payload)
                count += 1
            except Exception as exc:
                logger.error(f"[NotifyBulk] Failed for user {uid}: {exc}")
        return count

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    async def get_notifications(
        self,
        db: AsyncSession,
        user_id: UUID,
        page: int = 1,
        page_size: int = 20,
        unread_only: bool = False,
        notification_type: str | None = None,
    ) -> NotificationListResponse:
        """Paginated list of notifications for a user."""
        stmt = select(Notification).where(Notification.user_id == user_id)
        count_stmt = select(func.count()).select_from(Notification).where(Notification.user_id == user_id)

        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
            count_stmt = count_stmt.where(Notification.is_read.is_(False))

        if notification_type:
            stmt = stmt.where(Notification.notification_type == notification_type)
            count_stmt = count_stmt.where(Notification.notification_type == notification_type)

        stmt = stmt.order_by(Notification.created_at.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        result = await db.execute(stmt)
        notifications = result.scalars().all()

        total_result = await db.execute(count_stmt)
        total = total_result.scalar() or 0

        unread_count = await self.get_unread_count(db, user_id)

        return NotificationListResponse(
            items=[NotificationResponse.model_validate(n) for n in notifications],
            total=total,
            unread_count=unread_count,
            page=page,
            page_size=page_size,
        )

    async def get_unread_count(self, db: AsyncSession, user_id: UUID) -> int:
        """
        Get unread notification count.  Checks Redis cache first.
        """
        cache_key = _UNREAD_COUNTER_KEY.format(user_id=user_id)
        cached_count = await cache.get(cache_key)
        if cached_count is not None:
            return int(cached_count)

        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.is_read.is_(False))
        )
        result = await db.execute(stmt)
        count = result.scalar() or 0

        await cache.set(cache_key, count, _UNREAD_COUNTER_TTL)
        return count

    async def mark_as_read(self, db: AsyncSession, user_id: UUID, notification_ids: list[UUID]) -> int:
        """Mark specific notifications as read. Returns the number updated."""
        now = datetime.now()
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.id.in_(notification_ids),
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=now)
        )
        result = await db.execute(stmt)
        await db.commit()

        # Invalidate unread counter cache
        await self._invalidate_unread_cache(user_id)

        return result.rowcount  # type: ignore[return-value]

    async def mark_all_as_read(self, db: AsyncSession, user_id: UUID) -> int:
        """Mark all unread notifications as read for a user."""
        now = datetime.now()
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
            )
            .values(is_read=True, read_at=now)
        )
        result = await db.execute(stmt)
        await db.commit()

        await self._invalidate_unread_cache(user_id)
        return result.rowcount  # type: ignore[return-value]

    async def delete_notification(self, db: AsyncSession, user_id: UUID, notification_id: UUID) -> bool:
        """Delete a single notification. Returns True if deleted."""
        stmt = delete(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == user_id,
        )
        result = await db.execute(stmt)
        await db.commit()

        await self._invalidate_unread_cache(user_id)
        return (result.rowcount or 0) > 0

    async def delete_all_read(self, db: AsyncSession, user_id: UUID) -> int:
        """Delete all read notifications for a user."""
        stmt = delete(Notification).where(
            Notification.user_id == user_id,
            Notification.is_read.is_(True),
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    async def get_preferences(self, db: AsyncSession, user_id: UUID) -> NotificationPreferenceListResponse:
        """Get all notification preferences for a user."""
        stmt = select(NotificationPreference).where(NotificationPreference.user_id == user_id)
        result = await db.execute(stmt)
        prefs = result.scalars().all()
        return NotificationPreferenceListResponse(items=[NotificationPreferenceResponse.model_validate(p) for p in prefs])

    async def upsert_preference(
        self,
        db: AsyncSession,
        user_id: UUID,
        update_data: NotificationPreferenceUpdate,
    ) -> NotificationPreferenceResponse:
        """Create or update a notification preference."""
        stmt = select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.notification_type == update_data.notification_type,
        )
        result = await db.execute(stmt)
        pref = result.scalars().first()

        if pref:
            pref.in_app_enabled = update_data.in_app_enabled
            pref.email_enabled = update_data.email_enabled
            pref.push_enabled = update_data.push_enabled
        else:
            pref = NotificationPreference(
                user_id=user_id,
                notification_type=update_data.notification_type,
                in_app_enabled=update_data.in_app_enabled,
                email_enabled=update_data.email_enabled,
                push_enabled=update_data.push_enabled,
            )
            db.add(pref)

        await db.flush()
        await db.refresh(pref)
        await db.commit()

        return NotificationPreferenceResponse.model_validate(pref)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        db: AsyncSession,
        notification: Notification,
        response: NotificationResponse,
        user_email: str | None,
    ) -> None:
        """
        Route the notification to the appropriate channels.
        Email and push are dispatched as background tasks.
        """
        user_id = notification.user_id
        is_online = await notification_ws_manager.is_user_online(user_id)

        # --- In-app (WebSocket) delivery ---
        if notification.send_in_app:
            if is_online:
                unread_count = await self.get_unread_count(db, user_id)
                ws_event = WSNotificationEvent(
                    event="notification",
                    notification=response,
                    unread_count=unread_count,
                )
                delivered = await notification_ws_manager.send_to_user(user_id, ws_event.model_dump())
                new_status = DeliveryStatus.DELIVERED.value if delivered else DeliveryStatus.PENDING.value
            else:
                # User is offline — stays as PENDING; fetched via HTTP on reconnect
                new_status = DeliveryStatus.PENDING.value

            notification.in_app_status = new_status
            await db.commit()

        # Invalidate the cached unread counter after insert
        await self._invalidate_unread_cache(user_id)

        # --- Email delivery (background) ---
        if notification.send_email and user_email:
            asyncio.create_task(self._deliver_email(db, notification.id, user_email, notification.title, notification.body))

        # --- Push delivery (background) ---
        if notification.send_push and user_email:
            asyncio.create_task(self._deliver_push(db, notification.id, user_email, notification.title, notification.body))

    async def _deliver_email(
        self,
        db: AsyncSession,
        notification_id: UUID,
        recipient_email: str,
        title: str,
        body: str,
    ) -> None:
        """Background task for email delivery with status update."""
        try:
            success = await email_backend.deliver(recipient_email, title, body)
            new_status = DeliveryStatus.DELIVERED.value if success else DeliveryStatus.FAILED.value
        except Exception as exc:
            logger.error(f"[Email] Background delivery error: {exc}")
            new_status = DeliveryStatus.FAILED.value

        try:
            from app.config.database.session import SessionLocal

            async with SessionLocal() as session:
                stmt = (
                    update(Notification)
                    .where(Notification.id == notification_id)
                    .values(
                        email_status=new_status,
                        email_retry_count=Notification.email_retry_count + 1,
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as exc:
            logger.error(f"[Email] Failed to update delivery status: {exc}")

    async def _deliver_push(
        self,
        db: AsyncSession,
        notification_id: UUID,
        recipient_email: str,
        title: str,
        body: str,
    ) -> None:
        """Background task for push delivery with status update."""
        try:
            success = await push_backend.deliver(recipient_email, title, body)
            new_status = DeliveryStatus.DELIVERED.value if success else DeliveryStatus.FAILED.value
        except Exception as exc:
            logger.error(f"[Push] Background delivery error: {exc}")
            new_status = DeliveryStatus.FAILED.value

        try:
            from app.config.database.session import SessionLocal

            async with SessionLocal() as session:
                stmt = (
                    update(Notification)
                    .where(Notification.id == notification_id)
                    .values(
                        push_status=new_status,
                        push_retry_count=Notification.push_retry_count + 1,
                    )
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as exc:
            logger.error(f"[Push] Failed to update delivery status: {exc}")

    async def _get_preferences(self, db: AsyncSession, user_id: UUID, notification_type: str) -> dict[str, bool]:
        """
        Resolve user preferences for a notification type.
        Falls back to defaults if no preference row exists.
        """
        stmt = select(NotificationPreference).where(
            NotificationPreference.user_id == user_id,
            NotificationPreference.notification_type == notification_type,
        )
        result = await db.execute(stmt)
        pref = result.scalars().first()
        if pref:
            return {
                "in_app": pref.in_app_enabled,
                "email": pref.email_enabled,
                "push": pref.push_enabled,
            }
        # Defaults: in-app on, email & push off
        return {"in_app": True, "email": True, "push": True}

    async def _check_idempotency(self, db: AsyncSession, key: str) -> Notification | None:
        """Return existing notification if the idempotency key was already used."""
        stmt = select(Notification).where(Notification.idempotency_key == key)
        result = await db.execute(stmt)
        return result.scalars().first()

    async def _check_rate_limit(self, user_id: UUID) -> bool:
        """
        Sliding-window rate limiter backed by Redis.
        Returns True if the request is allowed.
        """
        key = _RATE_LIMIT_KEY.format(user_id=user_id)
        current = await cache.get(key)
        if current is not None and int(current) >= _RATE_LIMIT_MAX:
            return False
        # Increment counter (use Redis INCR via raw client for atomicity)
        if cache.redis_client:
            try:
                pipe = cache.redis_client.pipeline()
                pipe.incr(key)
                pipe.expire(key, _RATE_LIMIT_TTL)
                await pipe.execute()
            except Exception as exc:
                logger.warning(f"[RateLimit] Redis error: {exc}")
        return True

    async def _invalidate_unread_cache(self, user_id: UUID) -> None:
        """Remove the cached unread counter so the next read is fresh."""
        cache_key = _UNREAD_COUNTER_KEY.format(user_id=user_id)
        await cache.delete(cache_key)


# Singleton
notification_service = NotificationService()
