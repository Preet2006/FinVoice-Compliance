"""
Manager/head review of a single violation: confirm or mark false-alarm.
Per MASTER_IMPLEMENTATION_PLAN §7.4/§8.4: a caller identity is rejected
(403) even for their own call's violations — review is read-only for
callers. Score recalculation happens synchronously in the same request.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import RoleContext, get_role_context
from app.models import Call, Violation
from app.schemas import ReviewCallRef, ReviewOut, ReviewRequest, ReviewResponse
from app.services import reviewer_service, scoring_service
from app.services.access_control import visible_caller_ids

router = APIRouter(prefix="/api/violations", tags=["violations"])

VALID_DECISIONS = {"confirmed", "false_alarm"}


@router.post("/{violation_id}/review", response_model=ReviewResponse)
def review_violation(
    violation_id: str,
    body: ReviewRequest,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    violation = db.query(Violation).filter(Violation.id == violation_id).first()
    if violation is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "VIOLATION_NOT_FOUND",
                    "message": f"Violation {violation_id} does not exist or is not visible to this role.",
                }
            },
        )

    ids = visible_caller_ids(ctx.role, ctx.user_id, db)
    call = db.query(Call).filter(Call.id == violation.call_id).first()
    if ids is not None and (call is None or call.caller_id not in ids):
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "VIOLATION_NOT_FOUND",
                    "message": f"Violation {violation_id} does not exist or is not visible to this role.",
                }
            },
        )

    if ctx.role not in ("manager", "head"):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": "REVIEW_FORBIDDEN",
                    "message": "Only a manager or head of collections may review a violation.",
                }
            },
        )

    try:
        reviewer_service.guard_not_processing(db, call)  # 409 while the call is being re-analysed
    except reviewer_service.ReviewerActionError as exc:
        raise HTTPException(status_code=exc.status, detail={"error": {"code": exc.code, "message": exc.message}})

    if body.decision not in VALID_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "INVALID_DECISION",
                    "message": f"decision must be one of {sorted(VALID_DECISIONS)}.",
                }
            },
        )

    # Confirming a finding whose evidence failed validation does not make
    # that evidence valid. It needs an explicit attestation that the reviewer
    # checked the audio, recorded in the comment next to their name and time.
    if (
        violation.evidence_status == "unverified"
        and body.decision == "confirmed"
        and not (body.evidence_verified and (body.comment or "").strip())
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "EVIDENCE_VERIFICATION_REQUIRED",
                    "message": (
                        "This finding's evidence could not be verified against the transcript. "
                        "To confirm it, listen to the audio, set evidence_verified and explain "
                        "what you heard in the comment."
                    ),
                }
            },
        )

    review = violation.review
    review.decision = body.decision
    review.reviewer_id = ctx.user_id
    review.comment = body.comment
    review.reviewed_at = datetime.utcnow()
    reviewer_service.invalidate_completed_review(db, call, ctx.user_id, "a review decision was changed")

    call.score = scoring_service.compute_score(call.id, db)
    db.commit()

    return ReviewResponse(
        violation_id=violation.id,
        review=ReviewOut(
            decision=review.decision,
            reviewer=review.reviewer.name if review.reviewer else None,
            comment=review.comment,
            reviewed_at=review.reviewed_at,
        ),
        call=ReviewCallRef(call_id=call.id, score=call.score),
    )
