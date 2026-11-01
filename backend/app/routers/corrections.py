"""
Reviewer corrections: fix a speaker label, add a finding the analysis missed,
and read the audit trail. Manager / head only (a caller gets 403), scoped to
the calls that role may see (anything else is a 404, like every call route).
"""

import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.deps import RoleContext, get_role_context
from app.models import AppUser, ReviewerAudit, Violation
from app.routers.calls import _get_visible_call, segment_out, violation_out
from app.schemas import (
    AuditEntryOut,
    ManualFindingRequest,
    ManualFindingResponse,
    ReviewCallRef,
    ReviewCompleteOut,
    SpeakerCorrectionRequest,
    SpeakerCorrectionResponse,
)
from app.services import reviewer_service, scoring_service

router = APIRouter(prefix="/api/calls", tags=["corrections"])


def _require_reviewer(ctx: RoleContext, action: str) -> None:
    if ctx.role not in ("manager", "head"):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "REVIEW_FORBIDDEN", "message": f"Only a manager or head of collections may {action}."}},
        )


def _failure(exc: reviewer_service.ReviewerActionError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail={"error": {"code": exc.code, "message": exc.message}})


@router.post("/{call_id}/segments/{segment_id}/speaker", response_model=SpeakerCorrectionResponse)
def correct_speaker(
    call_id: str,
    segment_id: str,
    body: SpeakerCorrectionRequest,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    call = _get_visible_call(db, ctx, call_id)  # 404 when not visible, before any permission answer
    _require_reviewer(ctx, "correct a speaker label")
    try:
        segment = reviewer_service.correct_speaker(db, call, segment_id, body.speaker, body.comment, ctx.user)
    except reviewer_service.ReviewerActionError as exc:
        raise _failure(exc)
    return SpeakerCorrectionResponse(segment=segment_out(segment), call=ReviewCallRef(call_id=call.id, score=call.score))


@router.post("/{call_id}/violations", response_model=ManualFindingResponse, status_code=201)
def add_manual_finding(
    call_id: str,
    body: ManualFindingRequest,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    call = _get_visible_call(db, ctx, call_id)
    _require_reviewer(ctx, "add a finding")
    try:
        violation = reviewer_service.create_manual_finding(
            db, call, ctx.user, rule_code=body.rule_code, segment_id=body.segment_id,
            word_start=body.word_start, word_end=body.word_end, comment=body.comment, quote=body.quote,
        )
    except reviewer_service.ReviewerActionError as exc:
        raise _failure(exc)
    rows = (
        db.query(Violation).options(joinedload(Violation.segment), joinedload(Violation.review))
        .filter(Violation.call_id == call.id).all()
    )
    status = scoring_service.evaluate_violations(rows)[violation.id]
    return ManualFindingResponse(violation=violation_out(violation, status), call=ReviewCallRef(call_id=call.id, score=call.score))


@router.post("/{call_id}/review/complete", response_model=ReviewCompleteOut)
def complete_review(
    call_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    call = _get_visible_call(db, ctx, call_id)
    _require_reviewer(ctx, "complete a review")
    try:
        call = reviewer_service.complete_review(db, call, ctx.user)
    except reviewer_service.ReviewerActionError as exc:
        raise _failure(exc)
    return ReviewCompleteOut(
        call_id=call.id,
        review_status="reviewed",
        reviewed_by=call.review_completer.name if call.review_completer else None,
        reviewed_at=call.review_completed_at,
        score=call.score,
    )


@router.get("/{call_id}/audit", response_model=List[AuditEntryOut])
def get_audit_history(
    call_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    call = _get_visible_call(db, ctx, call_id)
    _require_reviewer(ctx, "view the correction history")
    rows = db.query(ReviewerAudit).filter(ReviewerAudit.call_id == call.id).order_by(ReviewerAudit.id).all()
    names = {u.id: u.name for u in db.query(AppUser).filter(AppUser.id.in_({r.actor_id for r in rows} or {""}))}
    return [
        AuditEntryOut(id=r.id, action=r.action, target_id=r.target_id, actor=names.get(r.actor_id),
                      detail=json.loads(r.detail), created_at=r.created_at)
        for r in rows
    ]
