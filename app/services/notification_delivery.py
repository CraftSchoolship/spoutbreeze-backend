"""
Pluggable delivery backends for email and push notifications.

Each backend exposes a single async ``deliver`` method.  The notification
service calls these in background tasks so the main request path is never
blocked.

Retry logic lives here — the service just calls ``deliver`` once; the
backend is responsible for exponential back-off and giving up after
``MAX_RETRIES``.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from app.config.logger_config import get_logger
from app.config.settings import get_settings

logger = get_logger("NotificationDelivery")
settings = get_settings()

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------
class DeliveryBackend(ABC):
    @abstractmethod
    async def deliver(
        self,
        recipient_email: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
        user_id: UUID | None = None,
    ) -> bool:
        """Attempt delivery.  Return True on success, False on permanent failure."""


# ---------------------------------------------------------------------------
# Email backend (SMTP / transactional API stub)
# ---------------------------------------------------------------------------
class EmailDeliveryBackend(DeliveryBackend):
    """
    Sends email notifications.

    In production, swap the inner ``_send`` implementation for your
    transactional provider (SendGrid, SES, Postmark, etc.).
    Currently logs the email for development/staging.
    """

    async def deliver(
        self,
        recipient_email: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
        user_id: UUID | None = None,
    ) -> bool:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                success = await self._send(recipient_email, title, body, data)
                if success:
                    logger.info(f"[Email] Delivered to {recipient_email}: {title}")
                    return True
            except Exception as exc:
                wait = BASE_BACKOFF_SECONDS**attempt
                logger.warning(
                    f"[Email] Attempt {attempt}/{MAX_RETRIES} failed for {recipient_email}: {exc}. Retrying in {wait}s …"
                )
                await asyncio.sleep(wait)

        logger.error(f"[Email] Permanently failed for {recipient_email}: {title}")
        return False

    async def _send(
        self,
        recipient_email: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """Send an email using the configured SMTP relay."""
        if not settings.smtp_password:
            logger.warning("[Email] SMTP password is not configured. Skipping email delivery.")
            return False

        message = EmailMessage()
        message["Subject"] = title
        message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
        message["To"] = recipient_email
        message.set_content(body)

        def _send_sync() -> None:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                server.ehlo()
                if settings.smtp_use_starttls:
                    server.starttls()
                    server.ehlo()
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(message)

        try:
            await asyncio.to_thread(_send_sync)
            logger.info(f"[Email] SMTP send success to {recipient_email}")
            return True
        except Exception as exc:
            logger.error(f"[Email] SMTP send failed to {recipient_email}: {exc}")
            return False


# ---------------------------------------------------------------------------
# Push notification backend (Firebase Cloud Messaging)
# ---------------------------------------------------------------------------
class PushDeliveryBackend(DeliveryBackend):
    """
    Sends push notifications via Firebase Cloud Messaging (FCM).

    Looks up all FCM registration tokens for the target user from the
    ``fcm_tokens`` table, then uses ``firebase_admin.messaging`` to send
    to each token.  Stale / unregistered tokens are automatically cleaned up.
    """

    async def deliver(
        self,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
        user_id: UUID | None = None,
    ) -> bool:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                success = await self._send(title, body, data, user_id)
                if success:
                    logger.info(f"[Push] Delivered to user {user_id}: {title}")
                    return True
            except Exception as exc:
                wait = BASE_BACKOFF_SECONDS**attempt
                logger.warning(
                    f"[Push] Attempt {attempt}/{MAX_RETRIES} failed for user {user_id}: {exc}. Retrying in {wait}s …"
                )
                await asyncio.sleep(wait)

        logger.error(f"[Push] Permanently failed for user {user_id}: {title}")
        return False

    async def _send(
        self,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
        user_id: UUID | None = None,
    ) -> bool:
        """Send push notification to all registered FCM tokens for a user."""
        from app.config.firebase_config import get_firebase_app

        app = get_firebase_app()
        if app is None:
            logger.warning("[Push] Firebase not initialised — skipping push delivery.")
            return False

        if user_id is None:
            logger.warning("[Push] No user_id provided — cannot look up FCM tokens.")
            return False

        # Look up tokens from the database
        tokens = await self._get_user_tokens(user_id)
        if not tokens:
            logger.info(f"[Push] No FCM tokens registered for user {user_id}.")
            return False

        # Build the FCM message and send to each token
        import firebase_admin.messaging as fcm_messaging

        messages = []
        for token in tokens:
            msg = fcm_messaging.Message(
                notification=fcm_messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={k: str(v) for k, v in data.items()} if data else None,
                token=token,
            )
            messages.append(msg)

        # Send all messages (run sync SDK call in executor to avoid blocking)
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: fcm_messaging.send_each(messages))

        # Clean up stale tokens
        stale_tokens: list[str] = []
        for i, send_response in enumerate(response.responses):
            if send_response.exception is not None:
                error = send_response.exception
                # Token is no longer valid — queue for deletion
                if isinstance(
                    error,
                    (
                        fcm_messaging.UnregisteredError,
                        fcm_messaging.SenderIdMismatchError,
                    ),
                ):
                    stale_tokens.append(tokens[i])
                    logger.info(f"[Push] Stale token removed for user {user_id}: {tokens[i][:20]}…")
                else:
                    logger.warning(f"[Push] FCM error for token {tokens[i][:20]}…: {error}")

        if stale_tokens:
            await self._remove_stale_tokens(stale_tokens)

        delivered = response.success_count > 0
        logger.info(
            f"[Push] FCM result for user {user_id}: {response.success_count} delivered, {response.failure_count} failed"
        )
        return delivered

    async def _get_user_tokens(self, user_id: UUID) -> list[str]:
        """Fetch all FCM tokens for a user from the database."""
        from sqlalchemy import select

        from app.config.database.session import SessionLocal
        from app.models.fcm_token_model import FCMToken

        async with SessionLocal() as session:
            stmt = select(FCMToken.token).where(FCMToken.user_id == user_id)
            result = await session.execute(stmt)
            return [row[0] for row in result.fetchall()]

    async def _remove_stale_tokens(self, tokens: list[str]) -> None:
        """Delete tokens that FCM reported as unregistered."""
        from sqlalchemy import delete

        from app.config.database.session import SessionLocal
        from app.models.fcm_token_model import FCMToken

        async with SessionLocal() as session:
            stmt = delete(FCMToken).where(FCMToken.token.in_(tokens))
            await session.execute(stmt)
            await session.commit()


# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------
email_backend = EmailDeliveryBackend()
push_backend = PushDeliveryBackend()
