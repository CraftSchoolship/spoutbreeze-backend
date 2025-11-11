"""merge heads

Revision ID: 1bb804ba4ace
Revises: 0635617e24e2, a1b2c3d4e5f6
Create Date: 2025-11-04 19:40:33.367402

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1bb804ba4ace'
down_revision: Union[str, None] = ('0635617e24e2', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
