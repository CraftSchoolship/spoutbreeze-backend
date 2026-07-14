"""Public contact endpoint — relays "Talk to the founders" messages from the
landing page to the team inbox via the configured SMTP relay."""

import html

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.config.logger_config import get_logger
from app.config.settings import get_settings
from app.utils.email import send_email
from app.utils.rate_limit import limiter

router = APIRouter(prefix="/api", tags=["Contact"])

logger = get_logger("Contact")
settings = get_settings()


class ContactRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    message: str = Field(..., min_length=10, max_length=5000)


@router.post("/contact")
@limiter.limit(lambda: settings.rate_limit_contact)
async def contact_founders(request: Request, payload: ContactRequest) -> dict[str, str]:
    """
    Accept a message from the public landing page and email it to the team.
    """
    recipient = settings.contact_email or settings.smtp_from_email
    subject = f"[BlueScale contact] Message from {payload.name}"
    text_body = (
        f"New message from the BlueScale landing page\n\n"
        f"Name:  {payload.name}\n"
        f"Email: {payload.email}\n\n"
        f"Message:\n{payload.message}\n"
    )
    safe_name = html.escape(payload.name)
    safe_email = html.escape(payload.email)
    safe_message = html.escape(payload.message)
    html_body = f"""
    <div style="font-family: sans-serif; max-width: 600px;">
      <h2 style="color: #0ea5e9;">New message from the BlueScale landing page</h2>
      <p><strong>Name:</strong> {safe_name}<br/>
         <strong>Email:</strong> <a href="mailto:{safe_email}">{safe_email}</a></p>
      <p style="white-space: pre-wrap; background: #f8fafc; border: 1px solid #e2e8f0;
                border-radius: 8px; padding: 16px;">{safe_message}</p>
    </div>
    """

    sent = await send_email(recipient, subject, text_body, html_body)
    if not sent:
        logger.error(f"[Contact] Failed to relay message from {payload.email}")
        raise HTTPException(
            status_code=502,
            detail="We couldn't send your message right now. Please try again later.",
        )

    logger.info(f"[Contact] Message from {payload.email} relayed to {recipient}")
    return {"status": "sent"}
