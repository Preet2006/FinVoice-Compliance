"""add_reviewer_corrections

Additive-only: reviewer speaker overrides on transcript_segment, an explicit review
sign-off (review_completed_by/at) on call, a source /
created_by pair on violation (AI vs manually added), and an append-only
reviewer_audit table. Existing rows keep their values (source defaults to
'ai', overrides are NULL); nothing is dropped, renamed or rewritten.

Revision ID: e5b81c2d4a07
Revises: c41f0e7a9b53
Create Date: 2026-10-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e5b81c2d4a07'
down_revision = 'c41f0e7a9b53'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode (recreate-and-copy) because SQLite cannot add CHECK / FK
    # constraints with a plain ALTER TABLE.
    with op.batch_alter_table('transcript_segment') as batch_op:
        batch_op.add_column(sa.Column('speaker_override', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('speaker_override_by', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('speaker_override_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('speaker_override_comment', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_segment_override_by', 'app_user', ['speaker_override_by'], ['id'])
        batch_op.create_check_constraint(
            'ck_segment_speaker_override',
            "speaker_override IS NULL OR speaker_override IN ('agent', 'borrower')",
        )
    with op.batch_alter_table('violation') as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(), nullable=False, server_default='ai'))
        batch_op.add_column(sa.Column('created_by', sa.String(), nullable=True))
        batch_op.create_foreign_key('fk_violation_created_by', 'app_user', ['created_by'], ['id'])
        batch_op.create_check_constraint('ck_violation_source', "source IN ('ai', 'manual')")
    with op.batch_alter_table('call') as batch_op:
        batch_op.add_column(sa.Column('review_completed_by', sa.String(), nullable=True))
        batch_op.add_column(sa.Column('review_completed_at', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key('fk_call_review_completed_by', 'app_user', ['review_completed_by'], ['id'])
    op.create_table(
        'reviewer_audit',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('call_id', sa.String(), sa.ForeignKey('call.id'), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('target_id', sa.String(), nullable=False),
        sa.Column('actor_id', sa.String(), sa.ForeignKey('app_user.id'), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
    )
    op.create_index('idx_reviewer_audit_call', 'reviewer_audit', ['call_id'])


def downgrade() -> None:
    op.drop_index('idx_reviewer_audit_call', table_name='reviewer_audit')
    op.drop_table('reviewer_audit')
    with op.batch_alter_table('call') as batch_op:
        batch_op.drop_constraint('fk_call_review_completed_by', type_='foreignkey')
        batch_op.drop_column('review_completed_at')
        batch_op.drop_column('review_completed_by')
    with op.batch_alter_table('violation') as batch_op:
        batch_op.drop_constraint('ck_violation_source', type_='check')
        batch_op.drop_constraint('fk_violation_created_by', type_='foreignkey')
        batch_op.drop_column('created_by')
        batch_op.drop_column('source')
    with op.batch_alter_table('transcript_segment') as batch_op:
        batch_op.drop_constraint('ck_segment_speaker_override', type_='check')
        batch_op.drop_constraint('fk_segment_override_by', type_='foreignkey')
        batch_op.drop_column('speaker_override_comment')
        batch_op.drop_column('speaker_override_at')
        batch_op.drop_column('speaker_override_by')
        batch_op.drop_column('speaker_override')
