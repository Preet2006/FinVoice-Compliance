"""
The evidence_status migration, run end to end on a throwaway SQLite file
(never the real database): upgrade, defaults, CHECK, downgrade, re-upgrade.
"""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app.config import settings

BACKEND_DIR = Path(__file__).resolve().parents[2]
PREVIOUS, CURRENT = "98bd1e6ee31d", "c41f0e7a9b53"


def alembic_config() -> Config:
    # No ini file: alembic's env.py would otherwise reconfigure logging
    # for the whole test session.
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def columns(engine):
    with engine.connect() as conn:
        return {r[1]: r for r in conn.execute(text("pragma table_info(violation)"))}


INSERT_OLD_ROW = text(
    "insert into violation (id, call_id, rule_code, severity, quote, created_at, needs_speaker_review) "
    "values ('v-old', 'CALL-X', 'R3', 'critical', 'old quote', datetime('now'), 1)"
)


@pytest.fixture()
def scratch_db(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'migration_test.db').as_posix()}"
    monkeypatch.setattr(settings, "database_url", url)
    engine = create_engine(url)
    yield engine
    engine.dispose()


def test_upgrade_adds_columns_defaults_existing_rows_and_enforces_the_check(scratch_db):
    command.upgrade(alembic_config(), PREVIOUS)
    assert "evidence_status" not in columns(scratch_db)
    with scratch_db.begin() as conn:
        conn.execute(INSERT_OLD_ROW)

    command.upgrade(alembic_config(), CURRENT)

    cols = columns(scratch_db)
    assert cols["evidence_status"][3] == 1  # NOT NULL
    assert cols["evidence_reason"][3] == 0  # nullable
    with scratch_db.connect() as conn:
        row = conn.execute(text("select quote, needs_speaker_review, evidence_status, evidence_reason from violation where id='v-old'")).one()
    assert tuple(row) == ("old quote", 1, "verified", None)  # existing data untouched, defaulted to verified

    with pytest.raises(IntegrityError):
        with scratch_db.begin() as conn:
            conn.execute(text(
                "insert into violation (id, call_id, rule_code, severity, quote, created_at, evidence_status) "
                "values ('v-bad', 'CALL-X', 'R3', 'critical', 'q', datetime('now'), 'bogus')"
            ))


def test_downgrade_removes_only_the_new_columns_and_keeps_data(scratch_db):
    command.upgrade(alembic_config(), CURRENT)
    with scratch_db.begin() as conn:
        conn.execute(INSERT_OLD_ROW)

    command.downgrade(alembic_config(), PREVIOUS)

    cols = columns(scratch_db)
    assert "evidence_status" not in cols and "evidence_reason" not in cols
    assert "needs_speaker_review" in cols  # the earlier migration is intact
    with scratch_db.connect() as conn:
        assert conn.execute(text("select quote from violation where id='v-old'")).scalar() == "old quote"

    command.upgrade(alembic_config(), CURRENT)  # and it can be re-applied
    assert "evidence_status" in columns(scratch_db)


def test_unverified_status_round_trips(scratch_db):
    command.upgrade(alembic_config(), CURRENT)
    with scratch_db.begin() as conn:
        conn.execute(text(
            "insert into violation (id, call_id, rule_code, severity, quote, created_at, evidence_status, evidence_reason) "
            "values ('v-u', 'CALL-X', 'R3', 'critical', 'seg text', datetime('now'), 'unverified', 'quote not found')"
        ))
        status, reason = conn.execute(text("select evidence_status, evidence_reason from violation where id='v-u'")).one()
    assert (status, reason) == ("unverified", "quote not found")
