"""
Batch audio + metadata CSV ingestion (Phase 5).

Per MASTER_IMPLEMENTATION_PLAN §10.2, the CSV join key between a metadata
row and an uploaded audio file is an explicit `filename` column — not a
filename-parsing convention — so a CSV row and an audio file are matched by
that column, not by assuming `call_01.wav` is always the first row.

This module only creates Call rows with status='queued' and stores audio
bytes to disk. It never starts transcription/LLM analysis (Phase 6+).
"""

import csv
import io
import re
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models import AppUser, Call, Office
from app.rules import IST

REQUIRED_CSV_COLUMNS = [
    "call_id",
    "filename",
    "caller_id",
    "office_id",
    "account_type",
    "start_time",
    "language",
]

ALLOWED_AUDIO_EXTENSIONS = {".wav", ".mp3"}
ALLOWED_ACCOUNT_TYPES = {"personal_loan", "credit_card"}

# Conservative allow-list: letters, digits, dot, underscore, hyphen only.
# Rejects path separators, "..", null bytes, and anything else that could
# be used for path traversal or an unsafe filesystem path.
SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def sanitize_filename(name: str) -> Optional[str]:
    """
    Returns the filename if it is safe to use as-is on disk, or None if it
    is unsafe (path traversal, path separators, hidden/empty name, wrong
    extension). Deliberately rejects rather than "cleans" an unsafe name —
    an upload with a bad filename should be rejected, not silently renamed.
    """
    if not name:
        return None
    # Only the basename is ever accepted; a name carrying directory
    # components is rejected outright rather than stripped.
    if name != Path(name).name:
        return None
    if ".." in name or name.startswith("."):
        return None
    if not SAFE_FILENAME_RE.match(name):
        return None
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        return None
    return name


def sniff_audio_content(filename: str, content: bytes) -> bool:
    """Light content validation so a renamed non-audio file is caught."""
    ext = Path(filename).suffix.lower()
    if ext == ".wav":
        return content[:4] == b"RIFF" and content[8:12] == b"WAVE"
    if ext == ".mp3":
        # ID3v2 tag, or an MPEG frame sync (0xFFE_ .. 0xFFF_).
        if content[:3] == b"ID3":
            return True
        return len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
    return False


def wav_duration_seconds(content: bytes) -> Optional[int]:
    """Real duration from WAV header, no estimate. None for non-WAV/invalid."""
    try:
        with wave.open(io.BytesIO(content), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate <= 0:
                return None
            return int(frames / rate)
    except (wave.Error, EOFError):
        return None


@dataclass
class CsvRow:
    line_number: int
    call_id: str = ""
    filename: str = ""
    caller_id: str = ""
    office_id: str = ""
    account_type: str = ""
    start_time: Optional[datetime] = None
    language: str = ""


@dataclass
class RejectedItem:
    identifier: str
    reason: str
    detail: Optional[str] = None


@dataclass
class AcceptedItem:
    call_id: str
    filename: str
    status: str = "queued"


@dataclass
class BatchResult:
    batch_id: str
    accepted: list = field(default_factory=list)
    rejected: list = field(default_factory=list)


class CsvFormatError(Exception):
    """Raised when the CSV is missing required columns entirely."""


def parse_metadata_csv(csv_bytes: bytes) -> list[CsvRow]:
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvFormatError("CSV is not valid UTF-8 text.") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CsvFormatError("CSV has no header row.")

    missing = [c for c in REQUIRED_CSV_COLUMNS if c not in reader.fieldnames]
    if missing:
        raise CsvFormatError(f"CSV is missing required column(s): {', '.join(missing)}")

    rows = []
    for i, raw in enumerate(reader, start=2):  # header is line 1
        rows.append(
            CsvRow(
                line_number=i,
                call_id=(raw.get("call_id") or "").strip(),
                filename=(raw.get("filename") or "").strip(),
                caller_id=(raw.get("caller_id") or "").strip(),
                office_id=(raw.get("office_id") or "").strip(),
                account_type=(raw.get("account_type") or "").strip(),
                start_time=_try_parse_timestamp(raw.get("start_time")),
                language=(raw.get("language") or "").strip(),
            )
        )
    return rows


# A date AND a time of day, optionally followed by a UTC offset ("Z", "+05:30").
# A bare date is rejected: it would silently become 00:00, which R1 would then
# report as an out-of-hours call that nobody actually made.
_TIMESTAMP_SHAPE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def _try_parse_timestamp(raw: Optional[str]) -> Optional[datetime]:
    """Returns a naive IST wall-clock datetime, or None if the text is not a
    full date-time. A timestamp with a UTC offset is converted to IST, because
    the database stores no offset and R1 reads the stored clock time; one
    without an offset is taken to already be IST."""
    if not raw:
        return None
    text = raw.strip()
    if not _TIMESTAMP_SHAPE.match(text):
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(IST).replace(tzinfo=None)
    return parsed


def process_batch(
    db: Session,
    csv_bytes: bytes,
    audio_files: list[tuple[str, bytes]],
    audio_dir: Path,
    max_upload_size_bytes: int,
) -> BatchResult:
    """
    audio_files: list of (original_filename, content_bytes) as received
    from the multipart upload — not yet validated or written to disk.
    """
    batch_id = f"b-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    result = BatchResult(batch_id=batch_id)

    # --- 1. Parse CSV (a totally malformed CSV rejects the whole batch;
    # per-row problems are handled per-row below). ---
    try:
        rows = parse_metadata_csv(csv_bytes)
    except CsvFormatError as exc:
        result.rejected.append(RejectedItem(identifier="metadata_csv", reason="invalid_csv", detail=str(exc)))
        return result

    # --- 2. Validate + sanitize uploaded audio files up front. ---
    valid_audio_by_name: dict[str, bytes] = {}
    for original_name, content in audio_files:
        safe_name = sanitize_filename(original_name)
        if safe_name is None:
            result.rejected.append(
                RejectedItem(identifier=original_name, reason="unsafe_or_unsupported_filename")
            )
            continue
        if safe_name in valid_audio_by_name:
            result.rejected.append(
                RejectedItem(identifier=original_name, reason="duplicate_filename_in_batch")
            )
            continue
        if len(content) > max_upload_size_bytes:
            result.rejected.append(RejectedItem(identifier=safe_name, reason="file_too_large"))
            continue
        if not sniff_audio_content(safe_name, content):
            result.rejected.append(RejectedItem(identifier=safe_name, reason="invalid_audio_content"))
            continue
        valid_audio_by_name[safe_name] = content

    # --- 3. Per-row CSV validation. ---
    seen_call_ids_in_csv: set[str] = set()
    office_ids = {o.id for o in db.query(Office.id)}
    caller_ids = {u.id for u in db.query(AppUser.id).filter(AppUser.role == "caller")}
    existing_call_ids = {c.id for c in db.query(Call.id)}

    valid_rows: list[CsvRow] = []
    for row in rows:
        ident = row.call_id or f"row {row.line_number}"

        if not row.call_id:
            result.rejected.append(RejectedItem(ident, "missing_call_id"))
            continue
        if row.call_id in seen_call_ids_in_csv:
            result.rejected.append(RejectedItem(ident, "duplicate_call_id_in_csv"))
            continue
        seen_call_ids_in_csv.add(row.call_id)

        if row.call_id in existing_call_ids:
            result.rejected.append(RejectedItem(ident, "duplicate_call_id"))
            continue
        if row.start_time is None:
            result.rejected.append(RejectedItem(ident, "invalid_timestamp"))
            continue
        if row.office_id not in office_ids:
            result.rejected.append(RejectedItem(ident, "unknown_office_id"))
            continue
        if row.caller_id not in caller_ids:
            result.rejected.append(RejectedItem(ident, "unknown_caller_id"))
            continue
        if row.account_type not in ALLOWED_ACCOUNT_TYPES:
            result.rejected.append(RejectedItem(ident, "invalid_account_type"))
            continue

        safe_filename = sanitize_filename(row.filename)
        if safe_filename is None:
            result.rejected.append(RejectedItem(ident, "unsafe_or_missing_filename_column"))
            continue

        valid_rows.append(row)

    # --- 4. Match valid rows <-> valid uploaded audio by filename. ---
    matched_filenames: set[str] = set()
    for row in valid_rows:
        safe_filename = sanitize_filename(row.filename)
        if safe_filename not in valid_audio_by_name:
            result.rejected.append(RejectedItem(row.call_id, "no_matching_audio_file"))
            continue
        matched_filenames.add(safe_filename)

        dest_path = audio_dir / safe_filename
        if dest_path.exists():
            # Never overwrite an existing recording, including one from a
            # prior batch or the hand-recorded ground-truth set.
            result.rejected.append(RejectedItem(row.call_id, "file_already_exists"))
            continue

        content = valid_audio_by_name[safe_filename]
        audio_dir.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(content)

        duration = wav_duration_seconds(content) if Path(safe_filename).suffix.lower() == ".wav" else None

        call = Call(
            id=row.call_id,
            caller_id=row.caller_id,
            office_id=row.office_id,
            account_type=row.account_type,
            start_time=row.start_time,
            duration_seconds=duration,
            language=row.language or None,
            has_audio=True,
            audio_path=f"data/audio/{safe_filename}",
            status="queued",
            is_ground_truth=False,
            batch_id=batch_id,
        )
        db.add(call)
        result.accepted.append(AcceptedItem(call_id=row.call_id, filename=safe_filename))

    db.commit()

    # --- 5. Any valid, uploaded audio file that no accepted row claimed. ---
    for name in valid_audio_by_name:
        if name not in matched_filenames:
            result.rejected.append(RejectedItem(name, "no_matching_call_id_in_csv"))

    return result
