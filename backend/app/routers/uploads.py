"""
Batch upload endpoints (Phase 5). Only a manager or the head of collections
may upload or read upload progress; a caller gets 403. The check runs before
the request body is read, so a refused request creates no call and saves no
file. (Roles are a prototype convention, not authentication.)
"""

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import RoleContext, get_role_context
from app.models import Call
from app.schemas import BatchStatusItem, BatchStatusResponse, UploadBatchResponse
from app.services import upload_service
from app.services.access_control import visible_caller_ids
from app.services.provider_errors import failure_kind_from_message, redact_text

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _require_uploader(ctx: RoleContext) -> None:
    if ctx.role not in ("manager", "head"):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "UPLOAD_FORBIDDEN", "message": "Only a manager or head of collections may upload calls."}},
        )


@router.post("/batch", response_model=UploadBatchResponse, status_code=202)
async def upload_batch(
    audio_files: list[UploadFile] = File(...),
    metadata_csv: UploadFile = File(...),
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    _require_uploader(ctx)  # before anything is read, parsed or written
    csv_bytes = await metadata_csv.read()
    audio_payload = [(f.filename or "", await f.read()) for f in audio_files]

    result = upload_service.process_batch(
        db=db,
        csv_bytes=csv_bytes,
        audio_files=audio_payload,
        audio_dir=Path(settings.audio_dir),
        max_upload_size_bytes=settings.max_upload_size_bytes,
    )

    return UploadBatchResponse(
        batch_id=result.batch_id,
        accepted=[
            {"call_id": a.call_id, "filename": a.filename, "status": a.status}
            for a in result.accepted
        ],
        rejected=[
            {"identifier": r.identifier, "reason": r.reason, "detail": r.detail}
            for r in result.rejected
        ],
    )


@router.get("/batch/{batch_id}", response_model=BatchStatusResponse)
def get_batch_status(
    batch_id: str,
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    _require_uploader(ctx)
    query = db.query(Call).filter(Call.batch_id == batch_id)
    ids = visible_caller_ids(ctx.role, ctx.user_id, db)
    if ids is not None:
        query = query.filter(Call.caller_id.in_(ids))

    calls = query.all()
    if not calls:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "BATCH_NOT_FOUND",
                    "message": f"Batch {batch_id} does not exist or has no calls visible to this role.",
                }
            },
        )

    return BatchStatusResponse(
        batch_id=batch_id,
        items=[
            BatchStatusItem(
                call_id=c.id,
                status=c.status,
                failure_reason=redact_text(c.failure_reason) if c.failure_reason else None,
                failure_kind=failure_kind_from_message(c.failure_reason),
            )
            for c in calls
        ],
    )
