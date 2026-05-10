"""user roles

Revision ID: 0005_user_roles
Revises: 0004_source_url_unique
Create Date: 2026-05-10

Adds a role column to users:
  'user' (default)  — regular product user
  'qa'              — QA / admin: can view any user's data, manage trainsets,
                      run optimization tooling

Auth middleware gates QA-only endpoints on this column. Tokens are still issued
the same way; the role just changes what endpoints the token can hit.

Also seeds the existing qa@curia.local account to role='qa' if it exists, so
the user we provisioned earlier doesn't need to be re-created.
"""

from alembic import op

revision = "0005_user_roles"
down_revision = "0004_source_url_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ADD COLUMN role TEXT NOT NULL DEFAULT 'user'
                CHECK (role IN ('user', 'qa'))
    """)
    # Promote any existing qa@curia.local account to qa role
    op.execute("""
        UPDATE users SET role = 'qa' WHERE email = 'qa@curia.local'
    """)
    op.execute("CREATE INDEX users_role_idx ON users (role)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS users_role_idx")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS role")
