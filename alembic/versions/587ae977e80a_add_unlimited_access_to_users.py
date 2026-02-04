"""add_unlimited_access_to_users

Revision ID: 587ae977e80a
Revises: c442d25966e9
Create Date: 2026-02-04 13:04:34.024305

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '587ae977e80a'
down_revision: Union[str, None] = 'c442d25966e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add unlimited_access column to users table
    op.add_column('users', sa.Column('unlimited_access', sa.Boolean(), nullable=False, server_default='false'))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove unlimited_access column from users table
    op.drop_column('users', 'unlimited_access')
