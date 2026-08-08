"""users.apple_refresh_token / apple_client_id — missing migration.

core/account.py (delete_account, to revoke the Apple grant on account
deletion per App Store 5.1.1(v)) and api/routes/auth.py (storing the
one-time Apple authorization exchange) have both read/written these two
columns since at least 0009_firebase_uid-era code — but no migration ever
created them. Found while clearing a local test account: delete_account
crashed with UndefinedColumnError on a fresh-migrated local DB, meaning
this only ever worked in environments where the columns existed via some
undocumented manual ALTER TABLE, not through the migration chain.

Revision ID: 0039_apple_refresh_token
Revises: 0038_user_location_country
"""

from alembic import op
import sqlalchemy as sa

revision = "0039_apple_refresh_token"
down_revision = "0038_user_location_country"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: prod already carries both columns from the undocumented
    # manual ALTER this migration formalizes — a blind add_column would crash
    # `alembic upgrade head` exactly where this fix matters most.
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_refresh_token TEXT")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS apple_client_id TEXT")


def downgrade() -> None:
    op.drop_column("users", "apple_client_id")
    op.drop_column("users", "apple_refresh_token")
