"""
Call listing, call detail, transcript, and violations endpoints.
Role scoping enforced via access_control.visible_caller_ids() on every
query — per MASTER_IMPLEMENTATION_PLAN §8.4, this is backend-enforced,
not merely hidden in the UI.

Out of scope for this phase (per plan Phase 3 vs later phases): uploads,
media streaming, reprocess, violation review (write), and analytics/*.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import RoleContext, get_role_context
from app.models import AppUser, Call, Office, TranscriptSegment, Violation, Rule, Review
from app.schemas import (
    CallDetail,
    CallListItem,
    R1CheckOut,
    CallerRef,
    OfficeRef,
    Page,
    ReprocessResponse,
    ReviewOut,
    TranscriptSegmentOut,
    ViolationOut,
)
from app.services import date_filters, outcome_service, pipeline, r1_check_service, scoring_service
from app.services.provider_errors import failure_kind_from_message, redact_text
from app.rules import get_severity_weight
from app.services.access_control import visible_caller_ids

router = APIRouter(prefix="/api/calls", tags=["calls"])


def _call_not_found(call_id: str) -> HTTPException:
    # Per §4.4/§8.4: a call outside the active role's visibility is
    # reported identically to a call that doesn't exist at all (404,
    # never 403) — defense-in-depth against enumeration.
    return HTTPException(
        status_code=404,
        detail={
            "error": {
                "code": "CALL_NOT_FOUND",
                "message": f"Call {call_id} does not exist or is not visible to this role.",
            }
        },
    )


def _scoped_call_query(db: Session, ctx: RoleContext):
    """Base Call query filtered to what this role/user may see."""
    q = db.query(Call)
    ids = visible_caller_ids(ctx.role, ctx.user_id, db)
    if ids is not None:
        q = q.filter(Call.caller_id.in_(ids))
    return q


def _get_visible_call(db: Session, ctx: RoleContext, call_id: str) -> Call:
    call = _scoped_call_query(db, ctx).filter(Call.id == call_id).first()
    if call is None:
        raise _call_not_found(call_id)
    return call


def segment_out(s) -> TranscriptSegmentOut:
    """A stored segment as the API shows it: `speaker` is the effective speaker
    (a reviewer's correction if there is one); the AI's own label stays visible."""
    corrected = s.speaker_override is not None
    return TranscriptSegmentOut(
        id=s.id,
        seq=s.seq,
        speaker=s.effective_speaker,
        start_time=float(s.start_time),
        end_time=float(s.end_time),
        text=s.text,
        sentiment=s.sentiment,
        speaker_confidence=float(s.speaker_confidence) if s.speaker_confidence is not None else None,
        speaker_uncertain=bool(s.speaker_uncertain),
        ai_speaker=s.speaker,
        speaker_corrected=corrected,
        corrected_by=s.override_reviewer.name if corrected and s.override_reviewer else None,
        corrected_at=s.speaker_override_at if corrected else None,
        correction_comment=s.speaker_override_comment if corrected else None,
    )


def violation_out(v, status) -> ViolationOut:
    review: Optional[Review] = v.review
    return ViolationOut(
        violation_id=v.id,
        rule_code=v.rule_code,
        rule_name=v.rule.name,
        rule_description=v.rule.description,
        severity=v.severity,
        segment_id=v.segment_id,
        start_time_seconds=float(v.start_time_seconds) if v.start_time_seconds is not None else None,
        quote=v.quote,
        explanation=v.explanation,
        confidence=float(v.confidence) if v.confidence is not None else None,
        needs_speaker_review=bool(v.needs_speaker_review),
        evidence_status=v.evidence_status or "verified",
        evidence_reason=redact_text(v.evidence_reason) if v.evidence_reason else None,
        counts_toward_score=status.counts_toward_score,
        score_deduction=get_severity_weight(v.severity) if status.counts_toward_score else 0,
        incident_group_id=status.incident_group_id,
        overlapping_rule_codes=list(status.overlapping_rule_codes),
        source=v.source or "ai",
        created_by=v.creator.name if (v.source or "ai") == "manual" and v.creator else None,
        review=ReviewOut(
            decision=review.decision if review else "pending",
            reviewer=review.reviewer.name if review and review.reviewer else None,
            comment=review.comment if review else None,
            reviewed_at=review.reviewed_at if review else None,
        ),
    )


def _safe_reason(reason):
    """Defense in depth: provider error text is already redacted when stored,
    but anything reaching the API is redacted again."""
    return redact_text(reason) if reason else None


def _outcome_fields(o) -> dict:
    return {
        "analysis_outcome": o.outcome,
        "counted_violations": o.counted_violations,
        "findings_pending_review": o.findings_pending_review,
        "findings_unverified": o.findings_unverified,
        "provisional_score": o.provisional_score,
        "last_reprocess_failed": o.last_reprocess_failed,
        "review_status": o.review_status,
        "findings_total": o.findings_total,
        "top_severity": o.top_severity,
    }


def _to_list_item(call: Call, outcome) -> CallListItem:
    return CallListItem(
        **_outcome_fields(outcome),
        call_id=call.id,
        caller=CallerRef(id=call.caller.id, name=call.caller.name),
        office=OfficeRef(id=call.office.id, name=call.office.name),
        start_time=call.start_time,
        duration_seconds=call.duration_seconds,
        language=call.language,
        account_type=call.account_type,
        score=call.score,
        status=call.status,
    )


@router.get("", response_model=Page[CallListItem])
def list_calls(
    office_id: Optional[str] = None,
    caller_id: Optional[str] = None,
    severity: Optional[str] = Query(default=None, pattern="^(critical|high|medium|low)$"),
    date_from: Optional[str] = None,  # IST date or datetime; see app.services.date_filters
    date_to: Optional[str] = None,
    q: Optional[str] = None,
    analysis_status: Optional[str] = Query(default=None, pattern="^(queued|processing|completed|failed)$"),
    review_status: Optional[str] = Query(default=None, pattern="^(pending_review|reviewed)$"),
    sort: str = Query(default="date_desc", pattern="^(date_desc|date_asc|score_asc|score_desc|risk)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    query = _scoped_call_query(db, ctx)

    if office_id:
        query = query.filter(Call.office_id == office_id)
    if caller_id:
        query = query.filter(Call.caller_id == caller_id)
    try:
        lower = date_filters.parse_filter_bound(date_from, upper=False)
        upper = date_filters.parse_filter_bound(date_to, upper=True)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "INVALID_DATE_FILTER", "message": "date_from and date_to must be a date (YYYY-MM-DD) or an ISO datetime."}},
        )
    if lower is not None:
        query = query.filter(Call.start_time >= lower)
    if upper is not None:
        query = query.filter(Call.start_time <= upper)
    if severity:
        query = query.join(Violation, Violation.call_id == Call.id).filter(
            Violation.severity == severity
        )
    if analysis_status:
        statuses = {"queued": ["queued"], "processing": ["transcribing", "analyzing"], "completed": ["ready"], "failed": ["failed"]}
        query = query.filter(Call.status.in_(statuses[analysis_status]))
    if review_status == "pending_review":
        query = query.filter(Call.status == "ready", Call.review_completed_at.is_(None))
    elif review_status == "reviewed":
        query = query.filter(Call.review_completed_at.isnot(None))
    if q:
        like = f"%{q}%"
        query = (
            query.join(AppUser, AppUser.id == Call.caller_id)
            .outerjoin(TranscriptSegment, TranscriptSegment.call_id == Call.id)
            .outerjoin(Violation, Violation.call_id == Call.id)
            .filter(
                or_(
                    Call.id.ilike(like),
                    AppUser.name.ilike(like),
                    TranscriptSegment.text.ilike(like),
                    Violation.quote.ilike(like),
                )
            )
        )

    query = query.distinct()
    total = query.count()
    # `risk` puts the lowest scores first (riskiest); calls with no score yet go last.
    order = {
        "date_desc": [Call.start_time.desc()],
        "date_asc": [Call.start_time.asc()],
        "score_asc": [Call.score.is_(None), Call.score.asc(), Call.start_time.desc()],
        "score_desc": [Call.score.is_(None), Call.score.desc(), Call.start_time.desc()],
        "risk": [Call.score.is_(None), Call.score.asc(), Call.start_time.desc()],
    }[sort]
    rows = (
        query.order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    outcomes = outcome_service.outcomes_for_calls(db, rows)
    return Page[CallListItem](
        items=[_to_list_item(c, outcomes[c.id]) for c in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{call_id}", response_model=CallDetail)
def get_call(
    call_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    call = _get_visible_call(db, ctx, call_id)
    r1_rows = db.query(Violation).filter(Violation.call_id == call.id, Violation.rule_code == "R1").all()
    return CallDetail(
        **_outcome_fields(outcome_service.outcome_for_call(db, call)),
        r1_check=R1CheckOut(**r1_check_service.r1_check(call, r1_rows).__dict__),
        call_id=call.id,
        caller=CallerRef(id=call.caller.id, name=call.caller.name),
        office=OfficeRef(id=call.office.id, name=call.office.name),
        start_time=call.start_time,
        duration_seconds=call.duration_seconds,
        language=call.language,
        account_type=call.account_type,
        score=call.score,
        sentiment=call.sentiment,
        agent_stayed_calm=call.agent_stayed_calm,
        status=call.status,
        has_audio=call.has_audio,
        is_ground_truth=call.is_ground_truth,
        failure_reason=_safe_reason(call.failure_reason),
        failure_kind=failure_kind_from_message(call.failure_reason),
        review_completed_by=call.review_completer.name if call.review_completer else None,
        review_completed_at=call.review_completed_at,
    )


@router.get("/{call_id}/transcript", response_model=list[TranscriptSegmentOut])
def get_call_transcript(
    call_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    # Visibility inherited from the parent call (§8.4): 404 if not visible,
    # even if the call_id otherwise exists in the database.
    _get_visible_call(db, ctx, call_id)
    segments = (
        db.query(TranscriptSegment)
        .filter(TranscriptSegment.call_id == call_id)
        .order_by(TranscriptSegment.seq)
        .all()
    )
    return [segment_out(s) for s in segments]


@router.post("/{call_id}/reprocess", response_model=ReprocessResponse)
def reprocess_call(
    call_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    # Visibility check first (404, not 403) — same as every other
    # call-scoped endpoint.
    call = _get_visible_call(db, ctx, call_id)

    # Reprocessing spends provider quota and replaces a call's analysis, so
    # it is a manager/head action (same rule as reviewing a violation).
    if ctx.role not in ("manager", "head"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "REPROCESS_FORBIDDEN",
                    "message": "Only a manager or head of collections may reprocess a call.",
                }
            },
        )

    try:
        call = pipeline.process_call(call.id, db)
    except pipeline.AlreadyProcessingError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "ALREADY_PROCESSING", "message": str(exc)}},
        )
    except pipeline.ReviewerWorkExistsError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "REVIEWER_WORK_EXISTS", "message": str(exc)}},
        )
    except pipeline.CallNotEligibleError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "CALL_NOT_ELIGIBLE", "message": str(exc)}},
        )

    return ReprocessResponse(
        call_id=call.id,
        status=call.status,
        score=call.score,
        failure_reason=_safe_reason(call.failure_reason),
        failure_kind=failure_kind_from_message(call.failure_reason),
    )


@router.get("/{call_id}/violations", response_model=list[ViolationOut])
def get_call_violations(
    call_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    _get_visible_call(db, ctx, call_id)
    violations = (
        db.query(Violation)
        .options(joinedload(Violation.segment), joinedload(Violation.review))
        .filter(Violation.call_id == call_id)
        .order_by(Violation.start_time_seconds)
        .all()
    )
    # Same function compute_score uses, so these flags always agree with the
    # displayed score.
    statuses = scoring_service.evaluate_violations(violations)
    return [violation_out(v, statuses[v.id]) for v in violations]
