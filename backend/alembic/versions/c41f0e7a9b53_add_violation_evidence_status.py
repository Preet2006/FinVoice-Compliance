"""add_violation_evidence_status

Additive-only: two columns on violation so a finding whose evidence failed
validation can be stored with an explicit status and reason instead of being
discarded. Existing rows become 'verified' (the column default); no existing
column is altered or dropped.

Revision ID: c41f0e7a9b53
Revises: 98bd1e6ee31d
Create Date: 2026-10-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c41f0e7a9b53'
down_revision = '98bd1e6ee31d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode (recreate-and-copy) because SQLite cannot add a
    # CHECK-constrained column with a plain ALTER TABLE.
    with op.batch_alter_table('violation') as batch_op:
        batch_op.add_column(
            sa.Column('evidence_status', sa.String(), nullable=False, server_default='verified')
        )
        batch_op.add_column(sa.Column('evidence_reason', sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            'ck_violation_evidence_status',
            "evidence_status IN ('verified', 'unverified')",
        )


def downgrade() -> None:
    with op.batch_alter_table('violation') as batch_op:
        batch_op.drop_constraint('ck_violation_evidence_status', type_='check')
        batch_op.drop_column('evidence_reason')
        batch_op.drop_column('evidence_status')
