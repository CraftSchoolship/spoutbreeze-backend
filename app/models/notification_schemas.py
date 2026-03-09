"""
Pydantic schemas for the notification system.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.notification_models import (
    NotificationPriority,
    NotificationType,
)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------
class NotificationCreate(BaseModel):
    """
    Internal schema used by services to create a notification.
    Not directly exposed as an HTTP body — the unified service builds this.
    """

    user_id: UUID
    notification_type: NotificationType
    title: str = Field(..., max_length=255)
    body: str
    data: str | None = None  # JSON-serialised extra payload
    priority: NotificationPriority = NotificationPriority.NORMAL
    send_in_app: bool = True
    send_email: bool = False
    send_push: bool = False
    idempotency_key: str | None = None


class NotificationBulkCreate(BaseModel):
    """Send one notification to many users (e.g. system announcements)."""

    user_ids: list[UUID]
    notification_type: NotificationType
    title: str = Field(..., max_length=255)
    body: str
    data: str | None = None
    priority: NotificationPriority = NotificationPriority.NORMAL
    send_in_app: bool = True
    send_email: bool = False
    send_push: bool = False


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------
class NotificationResponse(BaseModel):
    id: UUID
    user_id: UUID
    notification_type: str
    title: str
    body: str
    data: str | None = None
    priority: str
    send_in_app: bool
    send_email: bool
    send_push: bool
    in_app_status: str
    email_status: str
    push_status: str
    is_read: bool
    read_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int
    page: int
    page_size: int


class UnreadCountResponse(BaseModel):
    unread_count: int


class MarkReadRequest(BaseModel):
    notification_ids: list[UUID]


class MarkReadResponse(BaseModel):
    updated_count: int


# ---------------------------------------------------------------------------
# Preference schemas
# ---------------------------------------------------------------------------
class NotificationPreferenceUpdate(BaseModel):
    notification_type: str = Field(..., max_length=64)
    in_app_enabled: bool = True
    email_enabled: bool = False
    push_enabled: bool = False


class NotificationPreferenceResponse(BaseModel):
    id: UUID
    user_id: UUID
    notification_type: str
    in_app_enabled: bool
    email_enabled: bool
    push_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationPreferenceListResponse(BaseModel):
    items: list[NotificationPreferenceResponse]


# ---------------------------------------------------------------------------
# WebSocket event schemas
# ---------------------------------------------------------------------------
class WSNotificationEvent(BaseModel):
    """Payload sent over the WebSocket to clients."""

    event: str = "notification"
    notification: NotificationResponse
    unread_count: int
