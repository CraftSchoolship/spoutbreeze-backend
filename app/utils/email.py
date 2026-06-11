"""Standalone SMTP email sender.

Mirrors the SMTP logic in ``notification_delivery.EmailDeliveryBackend`` but
is callable outside the notification pipeline (e.g. the self-hosted password
reset flow). Uses the same ``SMTP_*`` settings.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

from app.config.logger_config import get_logger
from app.config.settings import get_settings

logger = get_logger("Email")
settings = get_settings()


async def send_email(
    recipient_email: str,
    subject: str,
    text_body: str,
    html_body: str | None = None,
) -> bool:
    """Send an email via the configured SMTP relay. Returns False (without
    raising) if SMTP isn't configured or the send fails, so callers can stay
    best-effort and avoid leaking delivery state to the client."""
    if not settings.smtp_password:
        logger.warning("[Email] SMTP password is not configured. Skipping email send.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{settings.smtp_from_name} <{settings.smtp_from_email}>"
    message["To"] = recipient_email
    message.set_content(text_body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    def _send_sync() -> None:
        if settings.smtp_use_starttls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(settings.smtp_username, settings.smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as server:
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
