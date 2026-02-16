"""empty message

Revision ID: ebd5dc3e1748
Revises: 1bb804ba4ace
Create Date: 2025-11-04 19:41:10.261357

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "ebd5dc3e1748"
down_revision: Union[str, None] = "1bb804ba4ace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # No-op: the duplicate migration that created these tables was removed.
    # Tables are now solely created by 97d5a800dc51.
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
