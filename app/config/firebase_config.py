"""
Firebase Admin SDK initialization.

Credentials are resolved from the ``FIREBASE_SERVICE_ACCOUNT_BASE64`` env var,
which should contain a base64-encoded JSON string of the service account.
(ideal for CI/CD and .env files).

If unavailable, the module gracefully disables push notifications.
"""

from __future__ import annotations

import base64
import json
import os

import firebase_admin
from firebase_admin import credentials

from app.config.logger_config import get_logger
from app.config.settings import get_settings

logger = get_logger("FirebaseConfig")
settings = get_settings()

_firebase_app: firebase_admin.App | None = None
_initialized: bool = False


def get_firebase_app() -> firebase_admin.App | None:
    """Return the initialised Firebase App, or None if unavailable."""
    global _firebase_app, _initialized

    if _initialized:
        return _firebase_app

    _initialized = True

    cred = _resolve_credentials()
    if cred is None:
        logger.warning(
            "[Firebase] No credentials found. Push notifications via FCM are disabled. "
            "Set FIREBASE_SERVICE_ACCOUNT_BASE64 env var."
        )
        return None

    try:
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("[Firebase] Admin SDK initialised successfully.")
        return _firebase_app
    except Exception as exc:
        logger.error(f"[Firebase] Failed to initialise Admin SDK: {exc}")
        _firebase_app = None
        return None


def _resolve_credentials() -> credentials.Certificate | None:
    """Read the base64 env var and decode it."""
    b64_json = settings.firebase_service_account_base64 or os.environ.get("FIREBASE_SERVICE_ACCOUNT_BASE64")
    if not b64_json:
        return None

    try:
        decoded = base64.b64decode(b64_json)
        service_info = json.loads(decoded)
        return credentials.Certificate(service_info)
    except Exception as exc:
        logger.error(f"[Firebase] Failed to decode FIREBASE_SERVICE_ACCOUNT_BASE64: {exc}")
        return None
