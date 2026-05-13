from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.logger_config import get_logger
from app.models.bbb_models import BbbMeeting
from app.models.connection_model import Connection
from app.models.event.event_models import Event, EventStatus
from app.models.organization_models import Organization
from app.models.payment_models import Subscription, SubscriptionStatus, Transaction
from app.models.stream_session_models import StreamSession, StreamSessionStatus
from app.models.user_models import User
from app.utils.datetime_utils import utcnow

logger = get_logger("AdminAnalyticsService")


class AdminAnalyticsService:
    """Compute platform-wide snapshot metrics for the admin dashboard."""

    @staticmethod
    async def _users_stats(db: AsyncSession) -> dict:
        now = utcnow()
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        total = (await db.execute(select(func.count(User.id)))).scalar_one()
        active = (await db.execute(select(func.count(User.id)).where(User.is_active.is_(True)))).scalar_one()
        new_7d = (await db.execute(select(func.count(User.id)).where(User.created_at >= d7))).scalar_one()
        new_30d = (await db.execute(select(func.count(User.id)).where(User.created_at >= d30))).scalar_one()

        # Role breakdown: roles is comma-separated, so compute in Python over a
        # lightweight projection. Population is small (admins/moderators); avoid
        # complex SQL string-splitting that doesn't port cleanly across dialects.
        rows = (await db.execute(select(User.roles))).scalars().all()
        by_role: dict[str, int] = {}
        for raw in rows:
            if not raw:
                continue
            for r in (s.strip() for s in raw.split(",")):
                if r:
                    by_role[r] = by_role.get(r, 0) + 1

        latest_q = select(User).order_by(User.created_at.desc()).limit(5)
        latest = (await db.execute(latest_q)).scalars().all()

        return {
            "total": total,
            "active": active,
            "inactive": total - active,
            "new_7d": new_7d,
            "new_30d": new_30d,
            "by_role": by_role,
            "latest": [
                {
                    "id": str(u.id),
                    "username": u.username,
                    "email": u.email,
                    "roles": u.roles,
                    "created_at": u.created_at,
                    "is_active": u.is_active,
                }
                for u in latest
            ],
        }

    @staticmethod
    async def _events_stats(db: AsyncSession) -> dict:
        now = utcnow()
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        total = (await db.execute(select(func.count(Event.id)))).scalar_one()
        created_7d = (await db.execute(select(func.count(Event.id)).where(Event.created_at >= d7))).scalar_one()
        created_30d = (await db.execute(select(func.count(Event.id)).where(Event.created_at >= d30))).scalar_one()

        status_rows = (await db.execute(select(Event.status, func.count(Event.id)).group_by(Event.status))).all()
        by_status: dict[str, int] = {s.value if hasattr(s, "value") else str(s): c for s, c in status_rows}
        for s in EventStatus:
            by_status.setdefault(s.value, 0)

        bbb_total = (await db.execute(select(func.count(BbbMeeting.id)))).scalar_one()
        bbb_30d = (await db.execute(select(func.count(BbbMeeting.id)).where(BbbMeeting.created_at >= d30))).scalar_one()

        latest_q = select(Event).order_by(Event.created_at.desc()).limit(5)
        latest = (await db.execute(latest_q)).scalars().all()

        return {
            "total": total,
            "by_status": by_status,
            "created_7d": created_7d,
            "created_30d": created_30d,
            "bbb_meetings_total": bbb_total,
            "bbb_meetings_30d": bbb_30d,
            "latest": [
                {
                    "id": str(e.id),
                    "title": e.title,
                    "status": e.status.value if hasattr(e.status, "value") else str(e.status),
                    "start_date": e.start_date,
                    "creator_id": str(e.creator_id),
                    "created_at": e.created_at,
                }
                for e in latest
            ],
        }

    @staticmethod
    async def _streaming_stats(db: AsyncSession) -> dict:
        now = utcnow()
        d1 = now - timedelta(days=1)
        d30 = now - timedelta(days=30)

        total = (await db.execute(select(func.count(StreamSession.id)))).scalar_one()
        sessions_24h = (
            await db.execute(select(func.count(StreamSession.id)).where(StreamSession.started_at >= d1))
        ).scalar_one()
        sessions_30d = (
            await db.execute(select(func.count(StreamSession.id)).where(StreamSession.started_at >= d30))
        ).scalar_one()
        active_now = (
            await db.execute(
                select(func.count(StreamSession.id)).where(StreamSession.status == StreamSessionStatus.ACTIVE.value)
            )
        ).scalar_one()

        platform_rows = (
            await db.execute(
                select(StreamSession.platform, func.count(StreamSession.id))
                .where(StreamSession.started_at >= d30)
                .group_by(StreamSession.platform)
            )
        ).all()
        by_platform: dict[str, int] = {(p or "unknown"): c for p, c in platform_rows}

        conn_rows = (
            await db.execute(
                select(Connection.provider, func.count(Connection.id))
                .where(Connection.revoked_at.is_(None))
                .group_by(Connection.provider)
            )
        ).all()
        connections_by_provider: dict[str, int] = {p: c for p, c in conn_rows}

        latest_q = select(StreamSession).order_by(StreamSession.started_at.desc()).limit(5)
        latest = (await db.execute(latest_q)).scalars().all()

        return {
            "sessions_total": total,
            "sessions_24h": sessions_24h,
            "sessions_30d": sessions_30d,
            "active_now": active_now,
            "by_platform": by_platform,
            "connections_by_provider": connections_by_provider,
            "latest": [
                {
                    "id": str(s.id),
                    "stream_id": s.stream_id,
                    "user_id": str(s.user_id),
                    "platform": s.platform,
                    "status": s.status,
                    "started_at": s.started_at,
                    "ended_at": s.ended_at,
                }
                for s in latest
            ],
        }

    @staticmethod
    async def _revenue_stats(db: AsyncSession) -> dict:
        now = utcnow()
        d7 = now - timedelta(days=7)
        d30 = now - timedelta(days=30)

        plan_rows = (
            await db.execute(select(Subscription.plan, func.count(Subscription.id)).group_by(Subscription.plan))
        ).all()
        subs_by_plan: dict[str, int] = {p: c for p, c in plan_rows}

        status_rows = (
            await db.execute(select(Subscription.status, func.count(Subscription.id)).group_by(Subscription.status))
        ).all()
        subs_by_status: dict[str, int] = {s: c for s, c in status_rows}

        active_subs = (
            await db.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.ACTIVE.value,
                            SubscriptionStatus.TRIALING.value,
                        ]
                    )
                )
            )
        ).scalar_one()

        # Revenue: sum of successful 'payment' transactions in last 30 days
        revenue_30d = (
            await db.execute(
                select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
                    Transaction.created_at >= d30,
                    Transaction.transaction_type == "payment",
                    Transaction.status.in_(["succeeded", "paid", "complete"]),
                )
            )
        ).scalar_one()

        transactions_30d_count = (
            await db.execute(select(func.count(Transaction.id)).where(Transaction.created_at >= d30))
        ).scalar_one()

        failed_payments_7d = (
            await db.execute(
                select(func.count(Transaction.id)).where(
                    Transaction.created_at >= d7,
                    Transaction.transaction_type.in_(["failed"]),
                )
            )
        ).scalar_one()

        latest_q = select(Transaction).order_by(Transaction.created_at.desc()).limit(5)
        latest = (await db.execute(latest_q)).scalars().all()

        return {
            "subs_by_plan": subs_by_plan,
            "subs_by_status": subs_by_status,
            "active_subscriptions": active_subs,
            "revenue_30d_usd": float(revenue_30d or 0.0),
            "transactions_30d_count": transactions_30d_count,
            "failed_payments_7d": failed_payments_7d,
            "latest_transactions": [
                {
                    "id": str(t.id),
                    "amount": t.amount,
                    "currency": t.currency,
                    "status": t.status,
                    "transaction_type": t.transaction_type,
                    "created_at": t.created_at,
                }
                for t in latest
            ],
        }

    @staticmethod
    async def _organizations_stats(db: AsyncSession) -> list[dict]:
        """
        Per-organization rollup. One row per existing Organization plus a
        synthetic "Unassigned" row (id=None) aggregating users with
        ``organization_id IS NULL``. Each metric is one GROUP BY query,
        stitched in Python — matches the existing one-query-per-metric style.
        """
        now = utcnow()
        d30 = now - timedelta(days=30)

        orgs_rows = (await db.execute(select(Organization.id, Organization.name))).all()
        rows: dict[object, dict] = {}
        for org_id, name in orgs_rows:
            rows[org_id] = {
                "id": org_id,
                "name": name,
                "user_count": 0,
                "active_users": 0,
                "events_total": 0,
                "bbb_meetings_total": 0,
                "streams_30d": 0,
                "active_subscriptions": 0,
                "revenue_30d_usd": 0.0,
            }
        rows[None] = {
            "id": None,
            "name": "Unassigned",
            "user_count": 0,
            "active_users": 0,
            "events_total": 0,
            "bbb_meetings_total": 0,
            "streams_30d": 0,
            "active_subscriptions": 0,
            "revenue_30d_usd": 0.0,
        }

        def _bucket(org_id):
            # Orgs may have been deleted between metric queries — fall back to Unassigned.
            return rows.get(org_id, rows[None])

        user_counts = (
            await db.execute(select(User.organization_id, func.count(User.id)).group_by(User.organization_id))
        ).all()
        for org_id, count in user_counts:
            _bucket(org_id)["user_count"] = count

        active_user_counts = (
            await db.execute(
                select(User.organization_id, func.count(User.id))
                .where(User.is_active.is_(True))
                .group_by(User.organization_id)
            )
        ).all()
        for org_id, count in active_user_counts:
            _bucket(org_id)["active_users"] = count

        event_counts = (
            await db.execute(
                select(User.organization_id, func.count(Event.id))
                .join(User, Event.creator_id == User.id)
                .group_by(User.organization_id)
            )
        ).all()
        for org_id, count in event_counts:
            _bucket(org_id)["events_total"] = count

        bbb_counts = (
            await db.execute(
                select(User.organization_id, func.count(BbbMeeting.id))
                .join(User, BbbMeeting.user_id == User.id)
                .group_by(User.organization_id)
            )
        ).all()
        for org_id, count in bbb_counts:
            _bucket(org_id)["bbb_meetings_total"] = count

        stream_counts = (
            await db.execute(
                select(User.organization_id, func.count(StreamSession.id))
                .join(User, StreamSession.user_id == User.id)
                .where(StreamSession.started_at >= d30)
                .group_by(User.organization_id)
            )
        ).all()
        for org_id, count in stream_counts:
            _bucket(org_id)["streams_30d"] = count

        sub_counts = (
            await db.execute(
                select(User.organization_id, func.count(Subscription.id))
                .join(User, Subscription.user_id == User.id)
                .where(
                    Subscription.status.in_(
                        [SubscriptionStatus.ACTIVE.value, SubscriptionStatus.TRIALING.value]
                    )
                )
                .group_by(User.organization_id)
            )
        ).all()
        for org_id, count in sub_counts:
            _bucket(org_id)["active_subscriptions"] = count

        revenue_rows = (
            await db.execute(
                select(User.organization_id, func.coalesce(func.sum(Transaction.amount), 0.0))
                .join(Subscription, Transaction.subscription_id == Subscription.id)
                .join(User, Subscription.user_id == User.id)
                .where(
                    Transaction.created_at >= d30,
                    Transaction.transaction_type == "payment",
                    Transaction.status.in_(["succeeded", "paid", "complete"]),
                )
                .group_by(User.organization_id)
            )
        ).all()
        for org_id, amount in revenue_rows:
            _bucket(org_id)["revenue_30d_usd"] = float(amount or 0.0)

        # Stable display order: real orgs A→Z, then Unassigned last.
        out = sorted(
            (r for k, r in rows.items() if k is not None),
            key=lambda r: r["name"].lower(),
        )
        unassigned = rows[None]
        # Only include the Unassigned row if it has any signal — keeps a clean dashboard
        # while still surfacing the bucket the moment there are unassigned users.
        if any(unassigned[k] for k in ("user_count", "events_total", "bbb_meetings_total", "streams_30d")):
            out.append(unassigned)
        return out

    @classmethod
    async def get_overview(cls, db: AsyncSession) -> dict:
        return {
            "generated_at": utcnow(),
            "users": await cls._users_stats(db),
            "events": await cls._events_stats(db),
            "streaming": await cls._streaming_stats(db),
            "revenue": await cls._revenue_stats(db),
            "organizations": await cls._organizations_stats(db),
        }
