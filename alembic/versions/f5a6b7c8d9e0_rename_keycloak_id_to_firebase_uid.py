"""rename users.keycloak_id to firebase_uid

Part of the Keycloak -> Firebase Auth migration. The Firebase UID for each
existing user is set to their old Keycloak ``sub`` during the one-time user
import (see scripts/migrate_users_to_firebase.py), so the column values stay
valid and only the name changes.

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-06-08 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("users", "keycloak_id", new_column_name="firebase_uid")
    # Keep index / constraint names consistent with the new column name.
    op.execute("ALTER INDEX IF EXISTS ix_users_keycloak_id RENAME TO ix_users_firebase_uid")


def downgrade() -> None:
    op.execute("ALTER INDEX IF EXISTS ix_users_firebase_uid RENAME TO ix_users_keycloak_id")
    op.alter_column("users", "firebase_uid", new_column_name="keycloak_id")
