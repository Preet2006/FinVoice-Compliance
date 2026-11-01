"""The speaker-state migration is tested only on disposable SQLite files."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
PREVIOUS, CURRENT = "e5b81c2d4a07", "f2a7c91d6e04"


def alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


@pytest.fixture()
def scratch_db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'speaker_states.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    engine = create_engine(url)
    yield engine
    engine.dispose()


def seed_segment(engine, speaker="agent", override=None):
    with engine.begin() as conn:
        conn.execute(text("insert into office (id, name, created_at) values ('o-1', 'O', datetime('now'))"))
        conn.execute(text("insert into app_user (id, name, role, office_id, created_at) values ('u-1', 'U', 'manager', 'o-1', datetime('now'))"))
        conn.execute(text("insert into call (id, caller_id, office_id, account_type, start_time, has_audio, status, is_ground_truth, created_at) values ('C1', 'u-1', 'o-1', 'personal_loan', datetime('now'), 1, 'ready', 0, datetime('now'))"))
        conn.execute(text("insert into transcript_segment (id, call_id, seq, speaker, start_time, end_time, text, speaker_override) values ('s1', 'C1', 0, :speaker, 0, 2, 'old text', :override)"), {"speaker": speaker, "override": override})


def test_upgrade_preserves_existing_labels_and_adds_uncertainty(scratch_db):
    engine = scratch_db
    command.upgrade(alembic_config(), PREVIOUS)
    seed_segment(engine, speaker="borrower", override="agent")
    command.upgrade(alembic_config(), CURRENT)
    with engine.connect() as conn:
        row = conn.execute(text("select speaker, speaker_override, speaker_uncertain from transcript_segment where id='s1'")).one()
    assert tuple(row) == ("borrower", "agent", 0)
    engine.dispose()


def test_new_states_are_allowed_and_invalid_states_are_rejected(scratch_db):
    engine = scratch_db
    command.upgrade(alembic_config(), CURRENT)
    seed_segment(engine)
    with engine.begin() as conn:
        for index, state in enumerate(("third_party", "unknown", "mixed"), start=2):
            conn.execute(text("insert into transcript_segment (id, call_id, seq, speaker, start_time, end_time, text) values (:id, 'C1', :seq, :speaker, 0, 1, 'state')"), {"id": f"s{index}", "seq": index, "speaker": state})
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(text("update transcript_segment set speaker='not-a-speaker' where id='s1'"))
    engine.dispose()


def test_downgrade_refuses_to_discard_new_states(scratch_db):
    engine = scratch_db
    command.upgrade(alembic_config(), CURRENT)
    seed_segment(engine, speaker="mixed")
    with pytest.raises(RuntimeError, match="new speaker values"):
        command.downgrade(alembic_config(), PREVIOUS)
    engine.dispose()
