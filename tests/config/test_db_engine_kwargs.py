"""Tests for the database engine-kwargs policy.

The previous code hard-coded ``echo=True`` on the production engine, which
flooded logs with raw SQL plus parameter values (UUIDs, emails, etc.). The
new ``build_engine_kwargs`` keeps echo off by default everywhere and
surfaces pool tuning as settings. These tests lock in the policy so a
future change can't silently re-enable echo.
"""

from app.config.database.session import build_engine_kwargs


class _S:
    """Minimal settings stand-in. Real Settings has many other fields the
    function under test never touches."""

    def __init__(
        self,
        db_echo: bool = False,
        db_pool_size: int = 20,
        db_max_overflow: int = 10,
        db_pool_pre_ping: bool = True,
    ):
        self.db_echo = db_echo
        self.db_pool_size = db_pool_size
        self.db_max_overflow = db_max_overflow
        self.db_pool_pre_ping = db_pool_pre_ping


def test_echo_is_off_by_default():
    kwargs = build_engine_kwargs(_S(), "postgresql+asyncpg://x/y")
    assert kwargs["echo"] is False


def test_echo_can_be_enabled_via_setting():
    kwargs = build_engine_kwargs(_S(db_echo=True), "postgresql+asyncpg://x/y")
    assert kwargs["echo"] is True


def test_postgres_url_includes_pool_args():
    kwargs = build_engine_kwargs(
        _S(db_pool_size=42, db_max_overflow=7, db_pool_pre_ping=True),
        "postgresql+asyncpg://x/y",
    )
    assert kwargs["pool_size"] == 42
    assert kwargs["max_overflow"] == 7
    assert kwargs["pool_pre_ping"] is True


def test_sqlite_url_omits_pool_args():
    """SQLite uses StaticPool, which rejects pool_size / max_overflow."""
    kwargs = build_engine_kwargs(_S(), "sqlite+aiosqlite:///./test.db")
    assert "pool_size" not in kwargs
    assert "max_overflow" not in kwargs
    # pool_pre_ping is harmless for sqlite, fine to keep
    assert "pool_pre_ping" in kwargs


def test_pool_pre_ping_can_be_disabled():
    kwargs = build_engine_kwargs(_S(db_pool_pre_ping=False), "postgresql+asyncpg://x/y")
    assert kwargs["pool_pre_ping"] is False
