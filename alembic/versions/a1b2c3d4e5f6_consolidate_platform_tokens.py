"""consolidate_platform_tokens_into_connections

Revision ID: a1b2c3d4e5f6
Revises: c3d4e5f6g7h8
Create Date: 2026-02-17 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "c3d4e5f6g7h8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create connections table
    op.create_table(
        "connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_user_id", sa.String(length=255), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_connections_id"), "connections", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_connections_provider"), "connections", ["provider"], unique=False
    )
    op.create_index(
        op.f("ix_connections_user_id"), "connections", ["user_id"], unique=False
    )
    # Partial unique index (only active connections)
    op.create_index(
        "ix_connections_user_provider_active",
        "connections",
        ["user_id", "provider"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )

    # Drop old tables
    op.drop_table("twitch_tokens")
    op.drop_table("youtube_tokens")


def downgrade() -> None:
    # Re-create youtube_tokens
    op.create_table(
        "youtube_tokens",
        sa.Column("id", postgresql.UUID(), autoincrement=False, nullable=False),
        sa.Column("user_id", postgresql.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "access_token", sa.VARCHAR(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "refresh_token", sa.VARCHAR(), autoincrement=False, nullable=True
        ),
        sa.Column(
            "expires_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "is_active", sa.BOOLEAN(), autoincrement=False, nullable=True
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=True
        ),
        sa.Column(
            "updated_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="youtube_tokens_user_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="youtube_tokens_pkey"),
    )

    # Re-create twitch_tokens
    op.create_table(
        "twitch_tokens",
        sa.Column("id", postgresql.UUID(), autoincrement=False, nullable=False),
        sa.Column("user_id", postgresql.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "access_token", sa.VARCHAR(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "refresh_token", sa.VARCHAR(), autoincrement=False, nullable=True
        ),
        sa.Column(
            "expires_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=False
        ),
        sa.Column(
            "created_at", postgresql.TIMESTAMP(), autoincrement=False, nullable=True
        ),
        sa.Column(
            "is_active", sa.BOOLEAN(), autoincrement=False, nullable=True
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="twitch_tokens_user_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="twitch_tokens_pkey"),
    )
    op.create_index(
        "ix_twitch_tokens_user_id", "twitch_tokens", ["user_id"], unique=False
    )
    op.create_index(
        "ix_twitch_tokens_id", "twitch_tokens", ["id"], unique=False
    )

    # Drop connections table
    op.drop_index(
        "ix_connections_user_provider_active",
        table_name="connections",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_index(op.f("ix_connections_user_id"), table_name="connections")
    op.drop_index(op.f("ix_connections_provider"), table_name="connections")
    op.drop_index(op.f("ix_connections_id"), table_name="connections")
    op.drop_table("connections")
