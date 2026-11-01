"""
The reviewer-corrections migration, run end to end on a throwaway SQLite file
(never the real database): additive upgrade over existing data, defaults,
CHECK constraints, append-only audit table, downgrade and re-upgrade.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
PREVIOUS, CURRENT = "c41f0e7a9b53", "7c4d9a1e2b6f"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def cols(engine, table):
    with engine.connect() as conn:
        return {r[1]: r for r in conn.execute(text(f"pragma table_info({table})"))}


@pytest.fixture()
def scratch_db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration_test.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    engine = create_engine(url)
    yield engine
    engine.dispose()


def seed_old_data(engine):
    with engine.begin() as conn:
        conn.execute(text("insert into office (id, name, created_at) values ('o-1', 'O', datetime('now'))"))
        conn.execute(text("insert into app_user (id, name, role, office_id, created_at) values ('u-1', 'U', 'manager', 'o-1', datetime('now'))"))
        conn.execute(text("insert into call (id, caller_id, office_id, account_type, start_time, has_audio, status, is_ground_truth, created_at) "
                          "values ('C1', 'u-1', 'o-1', 'personal_loan', datetime('now'), 1, 'ready', 0, datetime('now'))"))
        conn.execute(text("insert into transcript_segment (id, call_id, seq, speaker, start_time, end_time, text) values ('s1', 'C1', 0, 'agent', 0, 2, 'old text')"))
        conn.execute(text("insert into violation (id, call_id, rule_code, segment_id, severity, quote, created_at, needs_speaker_review) "
                          "values ('v1', 'C1', 'R3', 's1', 'critical', 'old quote', datetime('now'), 0)"))


def test_upgrade_is_additive_and_existing_rows_are_untouched(scratch_db):
    command.upgrade(alembic_config(), PREVIOUS)
    seed_old_data(scratch_db)
    before_tables = set(cols(scratch_db, "violation")) | set(cols(scratch_db, "transcript_segment"))

    command.upgrade(alembic_config(), CURRENT)

    assert before_tables <= set(cols(scratch_db, "violation")) | set(cols(scratch_db, "transcript_segment"))  # nothing dropped or renamed
    assert {"speaker_override", "speaker_override_by", "speaker_override_at", "speaker_override_comment"} <= set(cols(scratch_db, "transcript_segment"))
    assert {"source", "created_by"} <= set(cols(scratch_db, "violation"))
    assert {"review_completed_by", "review_completed_at"} <= set(cols(scratch_db, "call"))
    assert {"id", "call_id", "action", "target_id", "actor_id", "detail", "created_at"} == set(cols(scratch_db, "reviewer_audit"))
    with scratch_db.connect() as conn:
        seg = conn.execute(text("select speaker, text, speaker_override from transcript_segment where id='s1'")).one()
        vio = conn.execute(text("select quote, source, created_by from violation where id='v1'")).one()
        signed = conn.execute(text("select status, review_completed_by, review_completed_at from call where id='C1'")).one()
    assert tuple(seg) == ("agent", "old text", None)  # AI label intact, no override
    assert tuple(vio) == ("old quote", "ai", None)  # existing findings become AI findings
    assert tuple(signed) == ("ready", None, None)  # an existing analysed call is not retroactively "reviewed"


def test_check_constraints_are_enforced(scratch_db):
    command.upgrade(alembic_config(), CURRENT)
    seed_old_data(scratch_db)
    with pytest.raises(IntegrityError):
        with scratch_db.begin() as conn:
            conn.execute(text("update transcript_segment set speaker_override='not-a-speaker' where id='s1'"))
    with pytest.raises(IntegrityError):
        with scratch_db.begin() as conn:
            conn.execute(text("update violation set source='robot' where id='v1'"))
    with scratch_db.begin() as conn:
        conn.execute(text("update transcript_segment set speaker_override='borrower' where id='s1'"))  # valid values work


def test_downgrade_removes_only_the_new_objects_and_keeps_data(scratch_db):
    command.upgrade(alembic_config(), PREVIOUS)
    seed_old_data(scratch_db)
    command.upgrade(alembic_config(), CURRENT)

    command.downgrade(alembic_config(), PREVIOUS)

    assert "speaker_override" not in cols(scratch_db, "transcript_segment") and "source" not in cols(scratch_db, "violation")
    assert "review_completed_at" not in cols(scratch_db, "call") and "status" in cols(scratch_db, "call")
    assert "reviewer_audit" not in {r[0] for r in scratch_db.connect().execute(text("select name from sqlite_master where type='table'"))}
    with scratch_db.connect() as conn:
        assert conn.execute(text("select text from transcript_segment where id='s1'")).scalar() == "old text"
        assert conn.execute(text("select quote from violation where id='v1'")).scalar() == "old quote"
    command.upgrade(alembic_config(), CURRENT)  # and it can be applied again
    assert "speaker_override" in cols(scratch_db, "transcript_segment")


def test_the_migration_chain_has_a_single_head():
    from alembic.script import ScriptDirectory

    assert ScriptDirectory.from_config(alembic_config()).get_heads() == [CURRENT]
