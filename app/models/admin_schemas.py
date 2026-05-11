from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RecentUser(BaseModel):
    id: str
    username: str
    email: str
    roles: str
    created_at: datetime
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class RecentEvent(BaseModel):
    id: str
    title: str
    status: str
    start_date: datetime
    creator_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecentTransaction(BaseModel):
    id: str
    amount: float
    currency: str
    status: str
    transaction_type: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RecentStream(BaseModel):
    id: str
    stream_id: str
    user_id: str
    platform: str | None
    status: str
    started_at: datetime
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class UsersStats(BaseModel):
    total: int
    active: int
    inactive: int
    new_7d: int
    new_30d: int
    by_role: dict[str, int]
    latest: list[RecentUser]


class EventsStats(BaseModel):
    total: int
    by_status: dict[str, int]
    created_7d: int
    created_30d: int
    bbb_meetings_total: int
    bbb_meetings_30d: int
    latest: list[RecentEvent]


class StreamingStats(BaseModel):
    sessions_total: int
    sessions_24h: int
    sessions_30d: int
    active_now: int
    by_platform: dict[str, int]
    connections_by_provider: dict[str, int]
    latest: list[RecentStream]


class RevenueStats(BaseModel):
    subs_by_plan: dict[str, int]
    subs_by_status: dict[str, int]
    active_subscriptions: int
    revenue_30d_usd: float
    transactions_30d_count: int
    failed_payments_7d: int
    latest_transactions: list[RecentTransaction]


class AnalyticsOverview(BaseModel):
    generated_at: datetime
    users: UsersStats
    events: EventsStats
    streaming: StreamingStats
    revenue: RevenueStats
