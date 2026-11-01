"""preserve five-state speaker attribution and explicit uncertainty

Additive speaker-state change. Existing agent/borrower labels and reviewer
overrides are preserved; the new uncertainty flag defaults to false.

Revision ID: f2a7c91d6e04
Revises: e5b81c2d4a07
Create Date: 2026-10-30 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "f2a7c91d6e04"
down_revision = "e5b81c2d4a07"
branch_labels = None
depends_on = None


SPEAKER_STATES = "('agent', 'borrower', 'third_party', 'unknown', 'mixed')"


def upgrade() -> None:
    with op.batch_alter_table("transcript_segment") as batch_op:
        batch_op.add_column(
            sa.Column("speaker_uncertain", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.drop_constraint("ck_segment_speaker", type_="check")
        batch_op.create_check_constraint("ck_segment_speaker", f"speaker IN {SPEAKER_STATES}")
        batch_op.drop_constraint("ck_segment_speaker_override", type_="check")
        batch_op.create_check_constraint(
            "ck_segment_speaker_override",
            f"speaker_override IS NULL OR speaker_override IN {SPEAKER_STATES}",
        )


def downgrade() -> None:
    # Downgrade is intentionally conservative: it refuses to discard new
    # states rather than silently converting them to borrower.
    bind = op.get_bind()
    invalid = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM transcript_segment "
            "WHERE speaker NOT IN ('agent', 'borrower') "
            "OR (speaker_override IS NOT NULL AND speaker_override NOT IN ('agent', 'borrower'))"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError("Cannot downgrade speaker states while new speaker values exist")

    with op.batch_alter_table("transcript_segment") as batch_op:
        batch_op.drop_constraint("ck_segment_speaker", type_="check")
        batch_op.create_check_constraint("ck_segment_speaker", "speaker IN ('agent', 'borrower')")
        batch_op.drop_constraint("ck_segment_speaker_override", type_="check")
        batch_op.create_check_constraint(
            "ck_segment_speaker_override",
            "speaker_override IS NULL OR speaker_override IN ('agent', 'borrower')",
        )
        batch_op.drop_column("speaker_uncertain")
