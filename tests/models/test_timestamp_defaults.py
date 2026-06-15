"""Regression test: created_at/updated_at must be evaluated per-insert,
not once at import time.

The bug was `default=datetime.now()` (evaluated at module load) versus
`default=datetime.now` (callable, evaluated per-row). Without this guard,
every row inherits the same timestamp and the regression is silent until
an audit catches it.
"""

import asyncio
import uuid

import pytest

from app.models.channel.channels_model import Channel
from app.models.event.event_models import Event, EventStatus
from app.models.user_models import User


async def _make_user(db_session, suffix: str) -> User:
    user = User(
        firebase_uid=f"kc-{suffix}",
        username=f"user-{suffix}",
        email=f"{suffix}@example.com",
        first_name="F",
        last_name="L",
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.mark.anyio
async def test_user_created_at_differs_between_rows(db_session):
    u1 = await _make_user(db_session, "a")
    await asyncio.sleep(0.01)
    u2 = await _make_user(db_session, "b")
    assert u1.created_at != u2.created_at, (
        "User.created_at is shared across rows — `default=datetime.now()` regression. Use `default=datetime.now` (no parens)."
    )


@pytest.mark.anyio
async def test_channel_created_at_differs_between_rows(db_session):
    user = await _make_user(db_session, "ch")
    c1 = Channel(name="c1", creator_id=user.id)
    db_session.add(c1)
    await db_session.commit()
    await db_session.refresh(c1)

    await asyncio.sleep(0.01)

    c2 = Channel(name="c2", creator_id=user.id)
    db_session.add(c2)
    await db_session.commit()
    await db_session.refresh(c2)

    assert c1.created_at != c2.created_at


@pytest.mark.anyio
async def test_event_created_at_differs_between_rows(db_session):
    user = await _make_user(db_session, "ev")
    channel = Channel(name="event-channel", creator_id=user.id)
    db_session.add(channel)
    await db_session.commit()
    await db_session.refresh(channel)

    from datetime import datetime, timedelta

    base = datetime(2026, 5, 7, 12, 0, 0)

    def _new_event(title: str) -> Event:
        return Event(
            id=uuid.uuid4(),
            title=title,
            description="",
            occurs="once",
            start_date=base,
            end_date=base + timedelta(hours=1),
            start_time=base,
            timezone="UTC",
            creator_id=user.id,
            channel_id=channel.id,
            status=EventStatus.SCHEDULED,
        )

    e1 = _new_event("e1")
    db_session.add(e1)
    await db_session.commit()
    await db_session.refresh(e1)

    await asyncio.sleep(0.01)

    e2 = _new_event("e2")
    db_session.add(e2)
    await db_session.commit()
    await db_session.refresh(e2)

    assert e1.created_at != e2.created_at


def test_column_defaults_are_callables_not_fixed_values():
    """Belt-and-braces check at the column-definition level."""
    for cls in (User, Channel, Event):
        for col_name in ("created_at", "updated_at"):
            col = cls.__table__.c[col_name]
            assert callable(col.default.arg), (
                f"{cls.__name__}.{col_name} default must be a callable "
                f"(e.g. `datetime.now`), got fixed value {col.default.arg!r}"
            )
