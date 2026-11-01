"""add_speaker_confidence_and_review_flag

Additive-only: a nullable confidence column on transcript_segment and a
not-null (default false) review flag on violation. No existing column is
altered or dropped; no data is rewritten for existing rows beyond the new
column's default.

Revision ID: 98bd1e6ee31d
Revises: d56a1f75ef19
Create Date: 2026-10-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '98bd1e6ee31d'
down_revision = 'd56a1f75ef19'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite can't add a CHECK-constrained column with a plain ALTER TABLE,
    # so batch mode (recreate-and-copy) is used for both tables.
    with op.batch_alter_table('transcript_segment') as batch_op:
        batch_op.add_column(sa.Column('speaker_confidence', sa.Numeric(3, 2), nullable=True))
        batch_op.create_check_constraint(
            'ck_segment_speaker_confidence',
            'speaker_confidence IS NULL OR (speaker_confidence BETWEEN 0.0 AND 1.0)',
        )

    with op.batch_alter_table('violation') as batch_op:
        batch_op.add_column(
            sa.Column(
                'needs_speaker_review',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('violation') as batch_op:
        batch_op.drop_column('needs_speaker_review')

    with op.batch_alter_table('transcript_segment') as batch_op:
        batch_op.drop_constraint('ck_segment_speaker_confidence', type_='check')
        batch_op.drop_column('speaker_confidence')
