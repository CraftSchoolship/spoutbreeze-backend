"""
Firebase Admin SDK initialization.

Credentials are resolved in order:
  1. ``FIREBASE_SERVICE_ACCOUNT_JSON`` env var — the raw JSON string
     (ideal for deployments where you can't mount a file).
  2. ``firebase_service_account_path`` setting — path to the JSON file on disk
     (ideal for local development).

If neither is available the module gracefully disables push notifications.
"""

from __future__ import annotations

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
            "Set FIREBASE_SERVICE_ACCOUNT_JSON env var or place the JSON file at "
            f"'{settings.firebase_service_account_path}'."
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
    """Try env-var JSON first, then fall back to file on disk."""
    # --- Option 1: raw JSON string in env var ---
    raw_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if raw_json:
        try:
            service_info = json.loads(raw_json)
            return credentials.Certificate(service_info)
        except Exception as exc:
            logger.error(f"[Firebase] Failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {exc}")
            return None

    # --- Option 2: file path ---
    sa_path = settings.firebase_service_account_path
    if os.path.isfile(sa_path):
        try:
            return credentials.Certificate(sa_path)
        except Exception as exc:
            logger.error(f"[Firebase] Failed to load service-account file '{sa_path}': {exc}")
            return None

    return None
