"""add password hashes and server-managed authentication sessions

Revision ID: 7c4d9a1e2b6f
Revises: f2a7c91d6e04
"""

from alembic import op
import sqlalchemy as sa


revision = "7c4d9a1e2b6f"
down_revision = "f2a7c91d6e04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_user", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column(
        "app_user",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "auth_session",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("csrf_token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("idx_auth_session_token", "auth_session", ["token_hash"])
    op.create_index("idx_auth_session_user", "auth_session", ["user_id"])
    op.create_index("idx_auth_session_expiry", "auth_session", ["expires_at"])


def downgrade() -> None:
    op.drop_index("idx_auth_session_expiry", table_name="auth_session")
    op.drop_index("idx_auth_session_user", table_name="auth_session")
    op.drop_index("idx_auth_session_token", table_name="auth_session")
    op.drop_table("auth_session")
    op.drop_column("app_user", "is_active")
    op.drop_column("app_user", "password_hash")
