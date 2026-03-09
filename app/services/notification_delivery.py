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
from abc import ABC, abstractmethod
from typing import Any

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
        """
        TODO: Replace with real SMTP / API call.
        For now, just log and return True in non-production environments.
        """
        logger.info(f"[Email-Stub] Would send email to {recipient_email} — subject='{title}' body_length={len(body)}")
        # In development, treat as success so the pipeline keeps running.
        return True


# ---------------------------------------------------------------------------
# Push notification backend (FCM / APNs stub)
# ---------------------------------------------------------------------------
class PushDeliveryBackend(DeliveryBackend):
    """
    Sends push notifications via FCM / APNs.

    Requires a device token which is looked up from the user profile.
    Currently a stub — swap ``_send`` for your real provider SDK.
    """

    async def deliver(
        self,
        recipient_email: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                success = await self._send(recipient_email, title, body, data)
                if success:
                    logger.info(f"[Push] Delivered to {recipient_email}: {title}")
                    return True
            except Exception as exc:
                wait = BASE_BACKOFF_SECONDS**attempt
                logger.warning(
                    f"[Push] Attempt {attempt}/{MAX_RETRIES} failed for {recipient_email}: {exc}. Retrying in {wait}s …"
                )
                await asyncio.sleep(wait)

        logger.error(f"[Push] Permanently failed for {recipient_email}: {title}")
        return False

    async def _send(
        self,
        recipient_email: str,
        title: str,
        body: str,
        data: dict[str, Any] | None = None,
    ) -> bool:
        """
        TODO: Replace with real FCM / APNs call.
        """
        logger.info(f"[Push-Stub] Would send push to {recipient_email} — title='{title}' body_length={len(body)}")
        return True


# ---------------------------------------------------------------------------
# Singleton instances
# ---------------------------------------------------------------------------
email_backend = EmailDeliveryBackend()
push_backend = PushDeliveryBackend()
