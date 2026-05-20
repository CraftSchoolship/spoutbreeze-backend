"""add org onboarding: invites, domain verification, user.has_completed_onboarding

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-05-15 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # users.has_completed_onboarding — default False going forward; existing
    # accounts get TRUE so they never see /onboarding retroactively.
    op.add_column(
        "users",
        sa.Column(
            "has_completed_onboarding",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute("UPDATE users SET has_completed_onboarding = TRUE")
    # Drop the server_default now that the backfill is done so future inserts
    # rely on the ORM default (False) — matches the style of other bool columns.
    op.alter_column("users", "has_completed_onboarding", server_default=None)

    # organization_email_domains verification columns.
    op.add_column(
        "organization_email_domains",
        sa.Column("verified_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "organization_email_domains",
        sa.Column("verification_token", sa.String(), nullable=True),
    )
    op.add_column(
        "organization_email_domains",
        sa.Column("verification_started_at", sa.DateTime(), nullable=True),
    )
    # Any domain super-admin already registered is server-trusted.
    op.execute(
        "UPDATE organization_email_domains "
        "SET verified_at = CURRENT_TIMESTAMP WHERE verified_at IS NULL"
    )

    # organization_invites table.
    op.create_table(
        "organization_invites",
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_index(
        op.f("ix_organization_invites_organization_id"),
        "organization_invites",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_organization_invites_org_active",
        "organization_invites",
        ["organization_id", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organization_invites_org_active", table_name="organization_invites"
    )
    op.drop_index(
        op.f("ix_organization_invites_organization_id"),
        table_name="organization_invites",
    )
    op.drop_table("organization_invites")

    op.drop_column("organization_email_domains", "verification_started_at")
    op.drop_column("organization_email_domains", "verification_token")
    op.drop_column("organization_email_domains", "verified_at")

    op.drop_column("users", "has_completed_onboarding")
