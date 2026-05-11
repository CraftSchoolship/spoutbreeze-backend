"""Regression tests for the lazy Keycloak client factory.

Previously, `KeycloakOpenID(...)` was constructed at module import time
in `settings.py`. Any test or one-off script that imported `settings`
needed a reachable Keycloak server. The new `get_keycloak_openid()`
factory is `@lru_cache`d, so construction happens on first call rather
than at import.

These tests assert:
- Importing `settings` does not call the KeycloakOpenID constructor.
- `get_keycloak_openid()` is memoized — repeated calls return the same
  instance (cheap on the hot path, and tests can swap the cached
  instance via `monkeypatch`).
"""

import importlib
import sys
from unittest.mock import patch


def test_importing_settings_does_not_instantiate_keycloak():
    """If KeycloakOpenID were still being constructed at import time, the
    mock would record at least one call once `settings` was reloaded."""
    # Drop the cached module so the next import re-runs top-level code.
    for name in [
        "app.config.settings",
    ]:
        sys.modules.pop(name, None)

    with patch("keycloak.KeycloakOpenID") as kc_mock:
        importlib.import_module("app.config.settings")
        assert kc_mock.call_count == 0, (
            f"KeycloakOpenID was instantiated {kc_mock.call_count} time(s) "
            "at import — should be lazy via get_keycloak_openid()"
        )


def test_get_keycloak_openid_is_memoized():
    from app.config.settings import get_keycloak_openid

    # Clear lru_cache state so we observe a fresh memoization round.
    get_keycloak_openid.cache_clear()

    with patch("app.config.settings.KeycloakOpenID") as kc_mock:
        kc_mock.return_value = object()
        first = get_keycloak_openid()
        second = get_keycloak_openid()

    assert first is second, "Factory must return the cached instance"
    assert kc_mock.call_count == 1, f"KeycloakOpenID should be constructed exactly once, got {kc_mock.call_count}"

    # Leave no cached real-construction state behind for other tests.
    get_keycloak_openid.cache_clear()
