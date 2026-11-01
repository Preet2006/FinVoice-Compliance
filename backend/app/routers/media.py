"""
Audio streaming with HTTP Range support, per MASTER_IMPLEMENTATION_PLAN
§6.2/§4.3. Role visibility enforced the same way as every other
call-scoped endpoint (404, never 403, for a call outside the active
role's scope) — never relying on the frontend to hide a link.
"""

import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import RoleContext, get_role_context
from app.models import Call
from app.services.access_control import visible_caller_ids

router = APIRouter(prefix="/api/media", tags=["media"])

CONTENT_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


def _not_found(message: str) -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={"error": {"code": "MEDIA_NOT_FOUND", "message": message}},
    )


def _resolve_audio_path(call: Call) -> Path:
    """
    Resolves the real file for a call's audio, confined to the configured
    audio directory. `call.audio_path` is DB-controlled, not raw user
    input, but this guard is defense-in-depth against any future code
    path that might let an unexpected value reach here.
    """
    audio_dir = Path(settings.audio_dir).resolve()
    filename = Path(call.audio_path).name  # basename only, drops any path components
    candidate = (audio_dir / filename).resolve()
    if audio_dir not in candidate.parents and candidate != audio_dir:
        raise _not_found(f"Resolved audio path for call {call.id} is outside the audio directory.")
    return candidate


@router.get("/{call_id}")
def get_media(
    call_id: str,
    range_header: str | None = Header(default=None, alias="Range"),
    ctx: RoleContext = Depends(get_role_context),
    db: Session = Depends(get_db),
):
    ids = visible_caller_ids(ctx.role, ctx.user_id, db)
    query = db.query(Call).filter(Call.id == call_id)
    if ids is not None:
        query = query.filter(Call.caller_id.in_(ids))
    call = query.first()
    if call is None:
        raise _not_found(f"Call {call_id} does not exist or is not visible to this role.")

    if not call.has_audio or not call.audio_path:
        raise _not_found(f"Call {call_id} has no audio recording.")

    file_path = _resolve_audio_path(call)
    if not file_path.is_file():
        raise _not_found(f"Audio file for call {call_id} is missing on disk.")

    content_type = CONTENT_TYPES.get(file_path.suffix.lower(), "application/octet-stream")
    data = file_path.read_bytes()
    file_size = len(data)

    if range_header:
        match = RANGE_RE.match(range_header.strip())
        if not match:
            raise HTTPException(
                status_code=416,
                detail={"error": {"code": "INVALID_RANGE", "message": "Malformed Range header."}},
            )
        start_str, end_str = match.groups()
        start = int(start_str) if start_str else 0
        end = int(end_str) if end_str else file_size - 1
        end = min(end, file_size - 1)

        if start > end or start >= file_size:
            raise HTTPException(
                status_code=416,
                detail={"error": {"code": "RANGE_NOT_SATISFIABLE", "message": "Requested range is out of bounds."}},
                headers={"Content-Range": f"bytes */{file_size}"},
            )

        chunk = data[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
            },
        )

    return Response(
        content=data,
        status_code=200,
        media_type=content_type,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )
