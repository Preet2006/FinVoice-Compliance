"""
SQLAlchemy ORM models for Speech Analytics & Compliance system.
All 7 entities as specified in MASTER_IMPLEMENTATION_PLAN §6.1.
"""

from datetime import datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db import Base


class Office(Base):
    """Office locations (3: Jogeshwari, Lower Parel, Bangalore)."""

    __tablename__ = "office"

    id = Column(String, primary_key=True)  # e.g. 'o-01'
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Relationships
    users = relationship("AppUser", back_populates="office")
    calls = relationship("Call", back_populates="office")


class AppUser(Base):
    """User (caller, manager, or head of collections)."""

    __tablename__ = "app_user"

    id = Column(String, primary_key=True)  # e.g. 'u-014'
    name = Column(String, nullable=False)
    role = Column(
        String,
        nullable=False,
        # CheckConstraint will be added below for clarity
    )
    office_id = Column(String, ForeignKey("office.id"), nullable=False)
    manager_id = Column(String, ForeignKey("app_user.id"), nullable=True)
    password_hash = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="1")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("role IN ('caller', 'manager', 'head')", name="ck_app_user_role"),
        Index("idx_app_user_manager", "manager_id"),
        Index("idx_app_user_office", "office_id"),
    )

    # Relationships
    office = relationship("Office", back_populates="users")
    manager = relationship("AppUser", remote_side=[id], backref="direct_reports")
    calls = relationship("Call", back_populates="caller", foreign_keys="Call.caller_id")
    reviews = relationship("Review", back_populates="reviewer")
    sessions = relationship("AuthSession", back_populates="user", cascade="all, delete-orphan")


class AuthSession(Base):
    """Server-managed login session; only token hashes are persisted."""

    __tablename__ = "auth_session"

    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String, nullable=False, unique=True)
    csrf_token_hash = Column(String, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_auth_session_token", "token_hash"),
        Index("idx_auth_session_user", "user_id"),
        Index("idx_auth_session_expiry", "expires_at"),
    )

    user = relationship("AppUser", back_populates="sessions")


class Call(Base):
    """Call record with metadata, audio reference, and analysis results."""

    __tablename__ = "call"

    id = Column(String, primary_key=True)  # e.g. 'CALL-2041'
    caller_id = Column(String, ForeignKey("app_user.id"), nullable=False)
    office_id = Column(String, ForeignKey("office.id"), nullable=False)
    account_type = Column(
        String,
        nullable=False,
        # CheckConstraint for account_type
    )
    start_time = Column(DateTime, nullable=False)
    duration_seconds = Column(Integer, nullable=True)
    language = Column(String, nullable=True)  # dominant detected language code
    has_audio = Column(Boolean, nullable=False, default=False)
    audio_path = Column(String, nullable=True)  # NULL if has_audio=false
    status = Column(
        String,
        nullable=False,
        default="metadata_only",
        # CheckConstraint for status
    )
    failure_reason = Column(Text, nullable=True)
    score = Column(Integer, nullable=True)  # 0-100
    sentiment = Column(
        String,
        nullable=True,
        # CheckConstraint for sentiment
    )
    agent_stayed_calm = Column(Boolean, nullable=True)
    is_ground_truth = Column(Boolean, nullable=False, default=False)
    batch_id = Column(String, nullable=True)
    # Explicit manager sign-off. A call is 'reviewed' only when a manager or head
    # completes the review; finishing the AI analysis never sets this. Any later
    # reviewer change clears it again.
    review_completed_by = Column(String, ForeignKey("app_user.id"), nullable=True)
    review_completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "account_type IN ('personal_loan', 'credit_card')",
            name="ck_call_account_type",
        ),
        CheckConstraint(
            "status IN ('metadata_only', 'queued', 'transcribing', 'analyzing', 'ready', 'failed')",
            name="ck_call_status",
        ),
        CheckConstraint("score IS NULL OR (score BETWEEN 0 AND 100)", name="ck_call_score"),
        CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive', 'neutral', 'negative')",
            name="ck_call_sentiment",
        ),
        Index("idx_call_caller", "caller_id"),
        Index("idx_call_office", "office_id"),
        Index("idx_call_status", "status"),
        Index("idx_call_score", "score"),
        Index("idx_call_start", "start_time"),
    )

    # Relationships
    caller = relationship("AppUser", back_populates="calls", foreign_keys=[caller_id])
    office = relationship("Office", back_populates="calls")
    transcript_segments = relationship(
        "TranscriptSegment", back_populates="call", cascade="all, delete-orphan"
    )
    violations = relationship("Violation", back_populates="call", cascade="all, delete-orphan")
    review_completer = relationship("AppUser", foreign_keys=[review_completed_by])


class TranscriptSegment(Base):
    """Individual segment of transcribed speech with speaker label."""

    __tablename__ = "transcript_segment"

    id = Column(String, primary_key=True)  # e.g. 's-0041'
    call_id = Column(String, ForeignKey("call.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)  # ordering within the call
    speaker = Column(
        String,
        nullable=False,
        # CheckConstraint for speaker
    )
    start_time = Column(Numeric(8, 2), nullable=False)  # seconds
    end_time = Column(Numeric(8, 2), nullable=False)  # seconds
    text = Column(Text, nullable=False)
    sentiment = Column(
        String,
        nullable=True,
        # CheckConstraint for sentiment
    )
    # Uncalibrated model self-estimate of confidence in `speaker` for this
    # segment (0.0-1.0), as returned by the speaker-labelling LLM. NULL
    # means the model didn't provide one (never treated as high confidence
    # — see llm_service.is_uncertain_confidence).
    speaker_confidence = Column(Numeric(3, 2), nullable=True)
    # Explicit uncertainty from the unified speaker-labelling response. This
    # is separate from the role value so an uncertain agent/borrower label is
    # not silently presented as reliable.
    speaker_uncertain = Column(Boolean, nullable=False, default=False, server_default="0")
    # A reviewer's correction of `speaker`. `speaker` always stays the AI's own
    # label; `effective_speaker` (override if set, else the AI label) is what
    # scoring and the API use. Every correction is also appended to
    # reviewer_audit, so the history is never lost.
    speaker_override = Column(String, nullable=True)
    speaker_override_by = Column(String, ForeignKey("app_user.id"), nullable=True)
    speaker_override_at = Column(DateTime, nullable=True)
    speaker_override_comment = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "speaker IN ('agent', 'borrower', 'third_party', 'unknown', 'mixed')",
            name="ck_segment_speaker",
        ),
        CheckConstraint(
            "speaker_override IS NULL OR speaker_override IN ('agent', 'borrower', 'third_party', 'unknown', 'mixed')",
            name="ck_segment_speaker_override",
        ),
        CheckConstraint(
            "sentiment IS NULL OR sentiment IN ('positive', 'neutral', 'negative')",
            name="ck_segment_sentiment",
        ),
        CheckConstraint(
            "speaker_confidence IS NULL OR (speaker_confidence BETWEEN 0.0 AND 1.0)",
            name="ck_segment_speaker_confidence",
        ),
        UniqueConstraint("call_id", "seq", name="uq_segment_call_seq"),
        Index("idx_segment_call", "call_id"),
    )

    # Relationships
    call = relationship("Call", back_populates="transcript_segments")
    violations = relationship("Violation", back_populates="segment")
    override_reviewer = relationship("AppUser", foreign_keys=[speaker_override_by])

    @property
    def effective_speaker(self) -> str:
        return self.speaker_override or self.speaker


class Rule(Base):
    """Compliance rule definition (6 fixed rows: R1-R6)."""

    __tablename__ = "rule"

    code = Column(String, primary_key=True)  # 'R1'..'R6'
    name = Column(Text, nullable=False)
    description = Column(Text, nullable=False)
    default_severity = Column(
        String,
        nullable=False,
        # CheckConstraint for severity
    )

    __table_args__ = (
        CheckConstraint(
            "default_severity IN ('critical', 'high', 'medium', 'low')",
            name="ck_rule_severity",
        ),
    )

    # Relationships
    violations = relationship("Violation", back_populates="rule")


class Violation(Base):
    """Detected compliance breach with context and review status."""

    __tablename__ = "violation"

    id = Column(String, primary_key=True)  # e.g. 'v-CALL-0002-0001'
    call_id = Column(String, ForeignKey("call.id", ondelete="CASCADE"), nullable=False)
    rule_code = Column(String, ForeignKey("rule.code"), nullable=False)
    segment_id = Column(
        String, ForeignKey("transcript_segment.id"), nullable=True
    )  # NULL for R1
    severity = Column(
        String,
        nullable=False,
        # CheckConstraint for severity
    )
    start_time_seconds = Column(Numeric(8, 2), nullable=True)  # 0.0 for R1
    quote = Column(Text, nullable=False)
    explanation = Column(Text, nullable=True)
    confidence = Column(Numeric(3, 2), nullable=True)  # 0.0-1.0
    # True when the cited segment's speaker attribution is uncertain (not
    # labelled 'agent', or labelled 'agent' with missing/low confidence).
    # Gates automatic scoring (scoring_service) but never hides the
    # violation or its evidence — see pipeline.py.
    needs_speaker_review = Column(Boolean, nullable=False, default=False, server_default="0")
    # 'verified' = the quote was checked against the stored transcript.
    # 'unverified' = the analysis proposed this finding but its evidence
    # failed validation (invalid quote, nonexistent segment, or no evidence
    # at all). It is kept, shown to the manager and never deducts points
    # until a manager checks the audio and explicitly confirms it.
    evidence_status = Column(String, nullable=False, default="verified", server_default="verified")
    # Why the evidence failed validation, plus the model's proposed quote.
    evidence_reason = Column(Text, nullable=True)
    # 'ai' = produced by the analysis pipeline; 'manual' = added by a reviewer
    # (created_by). Manual findings are never produced or replaced by analysis.
    source = Column(String, nullable=False, default="ai", server_default="ai")
    created_by = Column(String, ForeignKey("app_user.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "severity IN ('critical', 'high', 'medium', 'low')", name="ck_violation_severity"
        ),
        CheckConstraint("source IN ('ai', 'manual')", name="ck_violation_source"),
        CheckConstraint(
            "evidence_status IN ('verified', 'unverified')", name="ck_violation_evidence_status"
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence BETWEEN 0.0 AND 1.0)",
            name="ck_violation_confidence",
        ),
        Index("idx_violation_call", "call_id"),
        Index("idx_violation_rule", "rule_code"),
    )

    # Relationships
    call = relationship("Call", back_populates="violations")
    rule = relationship("Rule", back_populates="violations")
    segment = relationship("TranscriptSegment", back_populates="violations")
    creator = relationship("AppUser", foreign_keys=[created_by])
    review = relationship(
        "Review", back_populates="violation", uselist=False, cascade="all, delete-orphan"
    )


class Review(Base):
    """Manager review decision for a violation."""

    __tablename__ = "review"

    id = Column(String, primary_key=True)
    violation_id = Column(
        String, ForeignKey("violation.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    decision = Column(
        String,
        nullable=False,
        default="pending",
        # CheckConstraint for decision
    )
    reviewer_id = Column(String, ForeignKey("app_user.id"), nullable=True)
    comment = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending', 'confirmed', 'false_alarm')", name="ck_review_decision"
        ),
    )

    # Relationships
    violation = relationship("Violation", back_populates="review")
    reviewer = relationship("AppUser", back_populates="reviews")


class ReviewerAudit(Base):
    """Append-only history of reviewer actions that change a call's analysis
    (speaker corrections, manually added findings). Rows are never updated or
    deleted by the application."""

    __tablename__ = "reviewer_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_id = Column(String, ForeignKey("call.id"), nullable=False)
    action = Column(String, nullable=False)  # 'speaker_correction' | 'manual_finding'
    target_id = Column(String, nullable=False)  # segment id or violation id
    actor_id = Column(String, ForeignKey("app_user.id"), nullable=False)
    detail = Column(Text, nullable=False)  # JSON: before/after, evidence, comment
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_reviewer_audit_call", "call_id"),)
