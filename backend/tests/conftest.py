"""
Shared pytest fixtures: an isolated in-memory SQLite database with a small,
deterministic fixture org chart — completely separate from the real seeded
backend/data/compliance.db, which these tests never touch.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import Base, get_db
from app.main import app
from app.config import settings
from app.models import AppUser, Call, Office, Rule, TranscriptSegment, Violation, Review
from app.rules import RULES

# Existing offline API fixtures use the historical identity headers. This is
# explicitly enabled only inside pytest; production settings default false.
settings.allow_legacy_headers = True


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    try:
        _seed_fixture(session)
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_fixture(db):
    """A fixed, small org chart used by every test: no randomness."""
    office_a = Office(id="o-01", name="Jogeshwari")
    office_b = Office(id="o-02", name="Lower Parel")
    db.add_all([office_a, office_b])
    db.flush()

    head = AppUser(id="u-head", name="Rajesh Kumar", role="head", office_id=office_a.id)
    mgr1 = AppUser(id="u-mgr1", name="Priya Sharma", role="manager", office_id=office_a.id)
    mgr2 = AppUser(id="u-mgr2", name="Vikram Singh", role="manager", office_id=office_b.id)
    caller1 = AppUser(
        id="u-caller1", name="Rahul Kumar", role="caller", office_id=office_a.id, manager_id=mgr1.id
    )
    caller2 = AppUser(
        id="u-caller2", name="Pooja Singh", role="caller", office_id=office_a.id, manager_id=mgr1.id
    )
    caller3 = AppUser(
        id="u-caller3", name="Imran Khan", role="caller", office_id=office_b.id, manager_id=mgr2.id
    )
    db.add_all([head, mgr1, mgr2, caller1, caller2, caller3])
    db.flush()

    for rule in RULES:
        db.add(Rule(**rule))
    db.flush()

    base = datetime(2026, 10, 1, 12, 0, 0)
    calls = [
        Call(
            id="CALL-C1-01",
            caller_id=caller1.id,
            office_id=office_a.id,
            account_type="personal_loan",
            start_time=base,
            duration_seconds=120,
            language="hi",
            has_audio=False,
            status="metadata_only",
            score=80,
        ),
        Call(
            id="CALL-C2-01",
            caller_id=caller2.id,
            office_id=office_a.id,
            account_type="credit_card",
            start_time=base + timedelta(days=1),
            duration_seconds=90,
            language="en",
            has_audio=False,
            status="metadata_only",
            score=55,
        ),
        Call(
            id="CALL-C3-01",
            caller_id=caller3.id,
            office_id=office_b.id,
            account_type="personal_loan",
            start_time=base + timedelta(days=2),
            duration_seconds=200,
            language="hi-en",
            has_audio=True,
            status="ready",
            score=40,
        ),
    ]
    db.add_all(calls)
    db.flush()

    segment = TranscriptSegment(
        id="seg-1",
        call_id="CALL-C3-01",
        seq=1,
        speaker="agent",
        start_time=0,
        end_time=5,
        text="hum aapke ghar bhi aayenge",
        sentiment="negative",
        speaker_confidence=0.95,
    )
    db.add(segment)
    db.flush()

    violation = Violation(
        id="v-1",
        call_id="CALL-C3-01",
        rule_code="R3",
        segment_id=segment.id,
        severity="critical",
        start_time_seconds=1.0,
        quote="hum aapke ghar bhi aayenge",
        explanation="Threat of in-person visit.",
        confidence=0.9,
    )
    db.add(violation)
    db.flush()

    review = Review(id="r-1", violation_id=violation.id, decision="pending")
    db.add(review)

    db.commit()


@pytest.fixture()
def client(db_session):
    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# Convenience header sets for each fixture identity
HEAD_HEADERS = {"X-Role": "head", "X-User-Id": "u-head"}
MGR1_HEADERS = {"X-Role": "manager", "X-User-Id": "u-mgr1"}
MGR2_HEADERS = {"X-Role": "manager", "X-User-Id": "u-mgr2"}
CALLER1_HEADERS = {"X-Role": "caller", "X-User-Id": "u-caller1"}
CALLER2_HEADERS = {"X-Role": "caller", "X-User-Id": "u-caller2"}
CALLER3_HEADERS = {"X-Role": "caller", "X-User-Id": "u-caller3"}
