"""
Correction of `call.start_time` for the 20 ground-truth calls from metadata.csv.

`build_plan` is read-only and is what the dry run reports. `apply_plan` is the
only function that writes; it is guarded so that it cannot run unless:
  - the database is at the expected Alembic revision,
  - a backup exists whose bytes are IDENTICAL to the live database and which
    passes SQLite's integrity check,
  - exactly the expected number of rows would change, and only `start_time`,
  - none of the 20 calls has been analysed (an analysed call carries stored R1
    evidence that quotes the old time - see r1_check_service),
  - the changes it would make are IDENTICAL, row by row, to the dry-run report
    that was approved (`approved_changes`): anything that moved since then is
    refused rather than applied,
and it runs as ONE transaction that is rolled back unless every check passes,
including a proof that no other column of any call row changed.

Nothing here imports or runs the seeder, a migration, or a provider.
"""

import hashlib
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.rules import evaluate_r1
from app.services.upload_service import _try_parse_timestamp

EXPECTED_REVISION = "c41f0e7a9b53"
EXPECTED_ROWS = 20
# How SQLAlchemy stores a DateTime in SQLite; the new value must use the same
# text form so the application reads it back identically.
DB_TIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"
OTHER_COLUMNS_SENTINEL = "start_time"


class CorrectionRefused(Exception):
    """A guard failed; nothing was written."""


@dataclass
class Change:
    call_id: str
    before: str  # exactly as stored
    after: str  # exactly as it would be stored
    csv_value: str
    r1_before: str
    r1_after: str


@dataclass
class CorrectionPlan:
    revision: Optional[str]
    changes: list = field(default_factory=list)
    unchanged: list = field(default_factory=list)  # ids already equal to the CSV
    problems: list = field(default_factory=list)  # anything that blocks applying

    @property
    def ok(self) -> bool:
        return not self.problems and len(self.changes) == EXPECTED_ROWS


def _stored_to_datetime(text) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(text)) if text else None
    except ValueError:
        return None


def build_plan(con: sqlite3.Connection, csv_rows: list, expected_ids: list, expected_revision: str = EXPECTED_REVISION) -> CorrectionPlan:
    """Read-only. Compares each expected ground-truth call with its CSV row."""
    con.row_factory = sqlite3.Row
    revision_row = con.execute("select version_num from alembic_version").fetchall()
    revision = revision_row[0][0] if len(revision_row) == 1 else None
    plan = CorrectionPlan(revision=revision)
    if revision != expected_revision:
        plan.problems.append(f"database revision is {revision!r}, expected {expected_revision!r}")

    csv_by_id: dict = {}
    for row in csv_rows:
        call_id = (row.get("call_id") or "").strip()
        if call_id in csv_by_id:
            plan.problems.append(f"{call_id}: more than one CSV row")
        csv_by_id.setdefault(call_id, row)

    for call_id in expected_ids:
        row = csv_by_id.get(call_id)
        db = con.execute("select id, start_time, is_ground_truth from call where id=?", (call_id,)).fetchone()
        if row is None:
            plan.problems.append(f"{call_id}: no CSV row")
            continue
        if db is None:
            plan.problems.append(f"{call_id}: no call record")
            continue
        if not db["is_ground_truth"]:
            plan.problems.append(f"{call_id}: not a ground-truth call; refusing to touch it")
            continue
        csv_dt = _try_parse_timestamp(row.get("start_time"))
        if csv_dt is None:
            plan.problems.append(f"{call_id}: CSV start_time {row.get('start_time')!r} is not a full date-time")
            continue
        after = csv_dt.strftime(DB_TIME_FORMAT)
        before = str(db["start_time"])
        if before == after:
            plan.unchanged.append(call_id)
            continue
        plan.changes.append(
            Change(
                call_id, before, after, row["start_time"],
                evaluate_r1(_stored_to_datetime(before)).status, evaluate_r1(csv_dt).status,
            )
        )

    analysed = con.execute(
        "select call_id, count(*) n from (select call_id from violation union all select call_id from transcript_segment) "
        f"where call_id in ({','.join('?' * len(expected_ids))}) group by call_id",
        expected_ids,
    ).fetchall()
    for r in analysed:
        plan.problems.append(
            f"{r['call_id']}: already has analysis rows ({r['n']}); its stored R1 evidence would go stale. "
            "Use the evidence-refresh procedure instead of this correction."
        )
    if not plan.problems and len(plan.changes) != EXPECTED_ROWS:
        plan.problems.append(f"expected exactly {EXPECTED_ROWS} rows to change, found {len(plan.changes)}")
    return plan


def render_sql(plan: CorrectionPlan) -> str:
    lines = ["BEGIN IMMEDIATE;"]
    for c in plan.changes:
        lines.append(
            f"UPDATE call SET start_time = '{c.after}' "
            f"WHERE id = '{c.call_id}' AND start_time = '{c.before}' AND is_ground_truth = 1;  -- expect 1 row"
        )
    lines.append("-- verify: 20 rows changed in total, 0 other rows; then:")
    lines.append("COMMIT;")
    return "\n".join(lines)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_backup(db_path: Path, backup_path: Path) -> None:
    db_path, backup_path = Path(db_path), Path(backup_path)
    if not backup_path.is_file():
        raise CorrectionRefused(f"backup not found: {backup_path}")
    if backup_path.resolve() == db_path.resolve():
        raise CorrectionRefused("the backup must be a different file from the database")
    if _sha256(db_path) != _sha256(backup_path):
        raise CorrectionRefused("the backup is not byte-identical to the live database; make a fresh backup")
    con = sqlite3.connect(f"file:{backup_path.as_posix()}?mode=ro", uri=True)
    try:
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise CorrectionRefused("the backup fails SQLite's integrity check")
    finally:
        con.close()


def _other_columns_digest(con: sqlite3.Connection) -> str:
    """Hash of every column of every call row EXCEPT start_time."""
    cols = [r[1] for r in con.execute("pragma table_info(call)") if r[1] != OTHER_COLUMNS_SENTINEL]
    h = hashlib.sha256()
    for row in con.execute(f"select {','.join(cols)} from call order by id"):
        h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def _signature(changes) -> list:
    return sorted((c["call_id"], c["before"], c["after"]) if isinstance(c, dict) else (c.call_id, c.before, c.after) for c in changes)


def apply_plan(db_path, csv_rows: list, expected_ids: list, backup_path, approved_changes: list, expected_revision: str = EXPECTED_REVISION) -> CorrectionPlan:
    """Applies the correction. Raises CorrectionRefused (having written nothing)
    if any guard fails; any failure inside the transaction is rolled back.

    `approved_changes` is the list of {call_id, before, after} from the dry-run
    report that was approved. The correction is applied only if the plan built
    under the write lock matches it exactly."""
    db_path = Path(db_path)
    if not approved_changes:
        raise CorrectionRefused("no approved dry-run report was supplied")
    verify_backup(db_path, backup_path)

    con = sqlite3.connect(db_path.as_posix(), isolation_level=None)  # explicit transaction control
    try:
        con.execute("BEGIN IMMEDIATE")  # takes the write lock first, so the plan cannot go stale under us
        plan = build_plan(con, csv_rows, expected_ids, expected_revision)
        if not plan.ok:
            raise CorrectionRefused("; ".join(plan.problems) or "plan is not valid")
        if _signature(plan.changes) != _signature(approved_changes):
            raise CorrectionRefused("the changes that would be made differ from the approved dry-run report; run a new dry run and approve it")

        total_before = con.execute("select count(*) from call").fetchone()[0]
        others_before = _other_columns_digest(con)
        untouched_times = {
            r[0]: r[1] for r in con.execute("select id, start_time from call") if r[0] not in {c.call_id for c in plan.changes}
        }

        for c in plan.changes:
            cur = con.execute(
                "update call set start_time = ? where id = ? and start_time = ? and is_ground_truth = 1",
                (c.after, c.call_id, c.before),
            )
            if cur.rowcount != 1:
                raise CorrectionRefused(f"{c.call_id}: expected to update exactly 1 row, updated {cur.rowcount}")

        if con.execute("select count(*) from call").fetchone()[0] != total_before:
            raise CorrectionRefused("the number of call rows changed")
        if _other_columns_digest(con) != others_before:
            raise CorrectionRefused("a column other than start_time changed")
        now = {r[0]: r[1] for r in con.execute("select id, start_time from call")}
        if any(now[i] != t for i, t in untouched_times.items()):
            raise CorrectionRefused("a start_time outside the 20 intended rows changed")
        if any(now[c.call_id] != c.after for c in plan.changes):
            raise CorrectionRefused("a corrected row does not hold its intended value")
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise CorrectionRefused("integrity check failed")
        con.execute("COMMIT")
        return plan
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
