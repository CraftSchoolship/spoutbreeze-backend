"""add stream_sessions table

Revision ID: b1c2d3e4f5a6
Revises: ada3c9110b40
Create Date: 2026-05-11 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "ada3c9110b40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stream_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("stream_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("platform", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_stream_sessions_stream_id"),
        "stream_sessions",
        ["stream_id"],
        unique=True,
    )
    op.create_index(
        "ix_stream_sessions_user_started",
        "stream_sessions",
        ["user_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_stream_sessions_platform_started",
        "stream_sessions",
        ["platform", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_stream_sessions_status",
        "stream_sessions",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_stream_sessions_status", table_name="stream_sessions")
    op.drop_index("ix_stream_sessions_platform_started", table_name="stream_sessions")
    op.drop_index("ix_stream_sessions_user_started", table_name="stream_sessions")
    op.drop_index(op.f("ix_stream_sessions_stream_id"), table_name="stream_sessions")
    op.drop_table("stream_sessions")
