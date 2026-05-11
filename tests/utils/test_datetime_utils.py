"""Tests for the `utcnow()` shim that replaced `datetime.utcnow()`.

The shim has to satisfy two contracts:
1. Return naive datetimes (the payment models use `DateTime` columns
   without `timezone=True`; mixing aware + naive raises `TypeError` at
   comparison time).
2. Return UTC time, not local time — the original `datetime.utcnow()`
   returned UTC, and rows produced by both forms must compare consistently.
"""

from datetime import UTC, datetime

from app.utils.datetime_utils import utcnow


def test_utcnow_returns_naive_datetime():
    out = utcnow()
    assert isinstance(out, datetime)
    assert out.tzinfo is None, "utcnow() must return a naive datetime to match the DB schema"


def test_utcnow_is_actually_utc():
    """The returned naive value should equal `datetime.now(UTC)` stripped
    of its tzinfo, not local time."""
    before = datetime.now(UTC).replace(tzinfo=None)
    out = utcnow()
    after = datetime.now(UTC).replace(tzinfo=None)
    # `out` must be in the [before, after] window — proves it's UTC, not local.
    assert before <= out <= after


def test_utcnow_compares_with_naive_db_values():
    """Regression: tz-aware values would raise TypeError when compared to
    naive datetimes loaded from the existing DB columns."""
    db_naive = datetime(2026, 5, 8, 0, 0, 0)
    out = utcnow()
    # If `out` were tz-aware, this would raise TypeError.
    assert out > db_naive
