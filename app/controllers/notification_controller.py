from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database.session import get_db
from app.config.logger_config import get_logger
from app.config.notification_ws_manager import notification_ws_manager
from app.controllers.user_controller import get_current_user
from app.models.notification_schemas import (
    MarkReadRequest,
    MarkReadResponse,
    NotificationListResponse,
    NotificationPreferenceListResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    UnreadCountResponse,
)
from app.models.user_models import User
from app.services.auth_service import AuthService
from app.services.notification_service import notification_service

logger = get_logger("NotificationController")
auth_service = AuthService()

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    notification_type: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List notifications for the current user (paginated)."""
    return await notification_service.get_notifications(
        db=db,
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        unread_only=unread_only,
        notification_type=notification_type,
    )


@router.get("/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get the number of unread notifications."""
    count = await notification_service.get_unread_count(db, current_user.id)
    return UnreadCountResponse(unread_count=count)


@router.post("/mark-read", response_model=MarkReadResponse)
async def mark_notifications_read(
    body: MarkReadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark specific notifications as read."""
    updated = await notification_service.mark_as_read(db, current_user.id, body.notification_ids)
    return MarkReadResponse(updated_count=updated)


@router.post("/mark-all-read", response_model=MarkReadResponse)
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Mark all notifications as read."""
    updated = await notification_service.mark_all_as_read(db, current_user.id)
    return MarkReadResponse(updated_count=updated)


@router.delete("/{notification_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification(
    notification_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single notification."""
    deleted = await notification_service.delete_notification(db, current_user.id, notification_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_all_read_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete all read notifications."""
    count = await notification_service.delete_all_read(db, current_user.id)
    return {"deleted_count": count}


# ---------------------------------------------------------------------------
# Preferences endpoints
# ---------------------------------------------------------------------------


@router.get("/preferences", response_model=NotificationPreferenceListResponse)
async def get_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get all notification preferences for the current user."""
    return await notification_service.get_preferences(db, current_user.id)


@router.put("/preferences", response_model=NotificationPreferenceResponse)
async def update_preference(
    body: NotificationPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a notification preference for a specific type."""
    return await notification_service.upsert_preference(db, current_user.id, body)


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def notification_websocket(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    """
    WebSocket endpoint for real-time notification delivery.

    Authentication (two paths, tried in order):
      1. Cookie — `access_token` HTTP-only cookie sent in the WS upgrade headers.
      2. Message — client sends ``{"type": "auth", "token": "<access_token>"}``
         within 10 seconds of connecting (fallback for cross-origin setups).

    After successful auth, the server sends:
        { "event": "connected", "unread_count": N }
    Then pushes notification events:
        { "event": "notification", "notification": {...}, "unread_count": N }
    """
    import asyncio
    import json

    from sqlalchemy import select

    from app.config.redis_config import cache
    from app.models.user_models import User

    await websocket.accept()
    user_id: UUID | None = None

    async def _resolve_user(token: str) -> UUID | None:
        """Validate JWT and return the User's UUID, or None on failure."""
        try:
            payload = auth_service.validate_token(token)
            keycloak_id = payload.get("sub")
            if not keycloak_id:
                return None
            result = await db.execute(select(User).where(User.keycloak_id == keycloak_id))
            user = result.scalars().first()
            return user.id if user else None
        except Exception:
            return None

    async def _register(uid: UUID, ws: WebSocket) -> None:
        """Insert the WS connection into the manager and flag presence in Redis."""
        key = str(uid)
        if key not in notification_ws_manager._connections:
            notification_ws_manager._connections[key] = []
        notification_ws_manager._connections[key].append(ws)
        await cache.sadd("notifications:online_users", key)
        await cache.expire("notifications:online_users", 3600)

    try:
        # ------------------------------------------------------------------
        # Path 1: cookie already present in the upgrade request
        # ------------------------------------------------------------------
        cookie_token = websocket.cookies.get("access_token")
        if cookie_token:
            user_id = await _resolve_user(cookie_token)

        # ------------------------------------------------------------------
        # Path 2: wait for a JSON auth message (cross-origin fallback)
        # ------------------------------------------------------------------
        if user_id is None:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
            except TimeoutError:
                await websocket.close(code=4001, reason="Authentication timeout")
                return

            try:
                auth_msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.close(code=4002, reason="Invalid JSON")
                return

            msg_token = auth_msg.get("token")
            if msg_token:
                user_id = await _resolve_user(msg_token)

            if user_id is None:
                await websocket.close(code=4003, reason="Authentication failed")
                return

        # ------------------------------------------------------------------
        # Connected — register, send initial unread count, start main loop
        # ------------------------------------------------------------------
        await _register(user_id, websocket)
        logger.info(f"[WS] User {user_id} connected")

        unread = await notification_service.get_unread_count(db, user_id)
        await websocket.send_text(json.dumps({"event": "connected", "unread_count": unread}))

        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong"}))
                elif msg.get("type") == "mark_read":
                    ids = msg.get("notification_ids", [])
                    if ids:
                        await notification_service.mark_as_read(db, user_id, [UUID(i) for i in ids])
                        unread = await notification_service.get_unread_count(db, user_id)
                        await websocket.send_text(json.dumps({"event": "unread_count", "unread_count": unread}))
            except (json.JSONDecodeError, ValueError):
                pass

    except WebSocketDisconnect:
        logger.info(f"[WS] User {user_id} disconnected")
    except Exception as exc:
        logger.error(f"[WS] Unexpected error for user {user_id}: {exc}")
    finally:
        if user_id:
            await notification_ws_manager.disconnect(user_id, websocket)
