"""Render HTML email bodies from Jinja2 templates keyed by notification_type.

Templates live under ``app/templates/email/``. Naming convention:
``<notification_type>.html`` for per-type designs, ``_default.html`` as
fallback, ``_base.html`` as the shared layout.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

from app.config.logger_config import get_logger

logger = get_logger("EmailTemplateRenderer")

_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "email"


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATE_DIR)),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_email(notification_type: str, context: dict[str, Any]) -> str:
    """Render the HTML email for ``notification_type``, falling back to ``_default.html``.

    ``context`` is passed through to the template; expected keys include
    ``title``, ``body``, and optional ``data`` (dict from the notification payload).
    """
    env = _env()
    template_name = f"{notification_type}.html"
    try:
        template = env.get_template(template_name)
    except TemplateNotFound:
        template = env.get_template("_default.html")
    return template.render(**context)
