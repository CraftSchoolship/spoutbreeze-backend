"""add_default_resolution_to_user

Revision ID: 70b7006ac28a
Revises: 587ae977e80a
Create Date: 2026-02-06 10:45:47.518735

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '70b7006ac28a'
down_revision: Union[str, None] = '587ae977e80a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('default_resolution', sa.String(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'default_resolution')
