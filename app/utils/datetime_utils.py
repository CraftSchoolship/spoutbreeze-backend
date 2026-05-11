"""Datetime helpers.

`datetime.utcnow()` is deprecated in Python 3.12+ and slated for removal.
The canonical replacement is `datetime.now(timezone.utc)`, but that returns
a *tz-aware* value. The payment models use naive `DateTime` columns
(without `timezone=True`), so values loaded from the DB come back naive —
mixing aware and naive in the same comparison raises ``TypeError`` at
runtime.

`utcnow()` here returns a naive UTC datetime, matching the existing schema
and the deprecated stdlib behavior bit-for-bit while being forward-compatible.
If the schema is later migrated to ``DateTime(timezone=True)`` columns, drop
the ``.replace(tzinfo=None)`` and call sites work unchanged.
"""

from datetime import UTC, datetime


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime."""
    return datetime.now(UTC).replace(tzinfo=None)
