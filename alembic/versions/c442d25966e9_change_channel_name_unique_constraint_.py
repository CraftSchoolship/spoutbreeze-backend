"""change_channel_name_unique_constraint_to_composite

Revision ID: c442d25966e9
Revises: 97d5a800dc51
Create Date: 2025-12-11 14:33:40.698560

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c442d25966e9"
down_revision: Union[str, None] = "97d5a800dc51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Drop the old unique constraint on name only
    op.drop_constraint("channels_name_key", "channels", type_="unique")

    # Create new composite unique constraint on (name, creator_id)
    op.create_unique_constraint(
        "uq_channel_name_creator", "channels", ["name", "creator_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop the composite unique constraint
    op.drop_constraint("uq_channel_name_creator", "channels", type_="unique")

    # Restore the old unique constraint on name only
    op.create_unique_constraint("channels_name_key", "channels", ["name"])
