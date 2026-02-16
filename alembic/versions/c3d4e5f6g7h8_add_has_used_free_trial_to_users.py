"""Add has_used_free_trial column to users table

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-02-16 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6g7h8"
down_revision: Union[str, None] = "b2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "has_used_free_trial",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Back-fill: mark existing users who already have a free trial subscription
    # as having used their trial so they can't get another one
    op.execute(
        """
        UPDATE users
        SET has_used_free_trial = true
        WHERE id IN (
            SELECT user_id FROM subscriptions
            WHERE plan = 'free'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("users", "has_used_free_trial")
