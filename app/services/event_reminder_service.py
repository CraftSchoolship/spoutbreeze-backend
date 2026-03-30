"""
Event Reminder Service.

A scheduled background job that scans for upcoming recurring events and
sends EVENT_REMINDER notifications to the creator and all organizers.

Recurrence types supported (from the ``Event.occurs`` column):
  - ``daily``    → remind every day at the configured lead time
  - ``weekly``   → remind once per week
  - ``monthly``  → remind once per month
  - ``once``     → remind once before the event starts (single occurrence)

The job runs every 15 minutes (configured in main.py) and looks for events
whose ``start_time`` falls within the next ``REMINDER_LEAD_MINUTES`` window
that have **not** already been reminded (tracked via an idempotency key).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config.logger_config import get_logger
from app.models.event.event_models import Event, EventStatus
from app.models.notification_models import NotificationType
from app.models.notification_schemas import NotificationCreate
from app.models.user_models import User
from app.services.notification_service import notification_service

logger = get_logger("EventReminderService")

# How far in advance to send reminders (minutes)
REMINDER_LEAD_MINUTES = 30

# Recurrence patterns that qualify for repeated reminders
_RECURRING_OCCURS = {"daily", "weekly", "biweekly", "monthly", "yearly"}


class EventReminderService:
    """Scans upcoming events and dispatches reminder notifications."""

    @staticmethod
    async def send_due_reminders(db: AsyncSession) -> int:
        """
        Find events starting within the reminder window and send
        notifications to each participant (creator + organizers).

        Returns the number of reminder notifications sent.
        """
        now = datetime.now()
        window_end = now + timedelta(minutes=REMINDER_LEAD_MINUTES)

        # Fetch scheduled events whose start_time is within [now, window_end]
        stmt = (
            select(Event)
            .options(
                selectinload(Event.organizers),
                selectinload(Event.creator),
            )
            .where(
                Event.status == EventStatus.SCHEDULED,
                Event.start_time >= now,
                Event.start_time <= window_end,
            )
        )

        result = await db.execute(stmt)
        events = result.scalars().all()

        if not events:
            return 0

        sent = 0

        for event in events:
            # Build the date key used for idempotency.
            # For recurring events we include today's date so the same event
            # can be reminded again on the next occurrence day.
            date_key = now.strftime("%Y-%m-%d")
            recipients = EventReminderService._collect_recipients(event)

            for user in recipients:
                idem_key = f"event_reminder:{event.id}:{user.id}:{date_key}"

                # Skip duplicates up front so sent count reflects newly-created reminders.
                existing = await notification_service._check_idempotency(db, idem_key)
                if existing is not None:
                    continue

                try:
                    payload = NotificationCreate(
                        user_id=user.id,
                        notification_type=NotificationType.EVENT_REMINDER,
                        title=f"Reminder: {event.title}",
                        body=EventReminderService._build_body(event),
                        data=json.dumps(
                            {
                                "event_id": str(event.id),
                                "event_title": event.title,
                                "start_time": event.start_time.isoformat(),
                                "occurs": event.occurs,
                            }
                        ),
                        send_in_app=True,
                        send_email=True,
                        send_push=True,
                        idempotency_key=idem_key,
                    )
                    await notification_service.notify(
                        db=db,
                        payload=payload,
                        user_email=user.email,
                    )
                    sent += 1
                    logger.info(f"[Reminder] Sent to {user.username} for event '{event.title}' (starts {event.start_time})")
                except ValueError as ve:
                    if "Duplicate" in str(ve) or "idempotency" in str(ve).lower():
                        # Already reminded — skip silently
                        pass
                    else:
                        logger.error(f"[Reminder] ValueError for {user.username}: {ve}")
                except Exception as exc:
                    logger.error(f"[Reminder] Failed for {user.username}, event '{event.title}': {exc}")

        logger.info(f"[Reminder] Job complete — {sent} reminders dispatched for {len(events)} events")
        return sent

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_recipients(event: Event) -> list[User]:
        """Return de-duplicated list of creator + organizers."""
        seen: set[str] = set()
        recipients: list[User] = []

        if event.creator:
            seen.add(str(event.creator.id))
            recipients.append(event.creator)

        for org in event.organizers:
            uid = str(org.id)
            if uid not in seen:
                seen.add(uid)
                recipients.append(org)

        return recipients

    @staticmethod
    def _build_body(event: Event) -> str:
        """Human-readable reminder body."""
        time_str = event.start_time.strftime("%I:%M %p")
        date_str = event.start_time.strftime("%B %d, %Y")
        occurs_label = event.occurs.capitalize() if event.occurs else "One-time"

        return (
            f'Your {occurs_label.lower()} event "{event.title}" is starting '
            f"soon at {time_str} on {date_str} ({event.timezone}). "
            f"Get ready!"
        )
