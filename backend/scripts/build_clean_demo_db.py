"""Create a clean demo database with login/reference data only.

The source database is read-only. The generated database contains offices,
users (including their existing password hashes), and compliance rules, but no
calls, audio references, transcripts, violations, reviews, or sessions.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import AppUser, Office, Rule  # noqa: E402


def configured_database() -> Path:
    value = settings.database_url.replace("sqlite:///", "", 1)
    path = Path(value)
    return path if path.is_absolute() else (BACKEND_DIR / path).resolve()


def build(source: Path, target: Path) -> None:
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("source and target must be different")
    if target.exists():
        raise ValueError(f"refusing to overwrite existing target: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    src_engine = create_engine(f"sqlite:///{source}")
    SourceSession = sessionmaker(bind=src_engine)
    src = SourceSession()
    try:
        offices = [Office(id=o.id, name=o.name, created_at=o.created_at) for o in src.query(Office).all()]
        users = [
            AppUser(
                id=u.id,
                name=u.name,
                role=u.role,
                office_id=u.office_id,
                manager_id=u.manager_id,
                password_hash=u.password_hash,
                is_active=u.is_active,
                created_at=u.created_at,
            )
            for u in src.query(AppUser).all()
        ]
        rules = [
            Rule(code=r.code, name=r.name, description=r.description, default_severity=r.default_severity)
            for r in src.query(Rule).all()
        ]
    finally:
        src.close()
        src_engine.dispose()

    engine = create_engine(f"sqlite:///{target}")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        db.add_all(offices)
        db.add_all(users)
        db.add_all(rules)
        db.flush()
        if db.query(AppUser).filter(AppUser.id.in_(["u-001", "u-002", "u-009"])).count() != 3:
            raise ValueError("required demo accounts u-001, u-002 and u-009 are missing")
        if db.query(AppUser).filter(AppUser.id.in_(["u-001", "u-002", "u-009"]), AppUser.password_hash.is_(None)).count():
            raise ValueError("one or more required demo accounts has no password hash")
        db.commit()
        with engine.begin() as conn:
            conn.execute(text("create table if not exists alembic_version (version_num varchar(32) not null)"))
            conn.execute(text("delete from alembic_version"))
            conn.execute(text("insert into alembic_version(version_num) values ('7c4d9a1e2b6f')"))
        with engine.connect() as conn:
            counts = {
                table: conn.execute(text(f"select count(*) from {table}")).scalar()
                for table in ("office", "app_user", "rule", "call", "transcript_segment", "violation", "review")
            }
            integrity = conn.execute(text("pragma integrity_check")).scalar()
            foreign_keys = conn.execute(text("pragma foreign_key_check")).fetchall()
            revision = conn.execute(text("select version_num from alembic_version")).scalar()
    finally:
        db.close()
        engine.dispose()

    if integrity != "ok" or foreign_keys:
        raise ValueError(f"target integrity failed: {integrity}, foreign keys={len(foreign_keys)}")
    print({"database": str(target), "row_counts": counts, "integrity": integrity, "migration": revision})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=str(configured_database()))
    parser.add_argument("--target", default=str(BACKEND_DIR / "data" / "company_demo_clean.db"))
    args = parser.parse_args()
    try:
        build(Path(args.source), Path(args.target))
    except (OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
