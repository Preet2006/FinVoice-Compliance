"""
Database seeding script for Speech Analytics & Compliance prototype.
Creates offices, users, rules, and ~2,000 call records (1,980 metadata + 20 with audio).

WARNING: This script WIPES all existing data and rebuilds from scratch.
Only run in development/demo environments.
"""

import sys
import random
from datetime import datetime, timedelta
from pathlib import Path

# Windows console defaults to cp1252, which can't encode the emoji in the
# status messages below; force UTF-8 so this runs in any terminal.
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.db import SessionLocal, engine
from app.models import (
    Office,
    AppUser,
    Rule,
    Call,
    Base,
)
from app.rules import RULES
from app.services.metadata_audit import load_csv_rows
from app.services.upload_service import _try_parse_timestamp

METADATA_CSV = Path(__file__).parent.parent / "data" / "metadata.csv"

# Permitted calling hours (R1): [08:00, 19:00). Seeded business-hours calls use
# hours 8..18 so every time they can take, whatever the minute and second, is
# inside the window.
BUSINESS_HOURS = (8, 18)


def at_time_of_day(base: datetime, days: int, hour: int, minute: int, second: int = 0) -> datetime:
    """The day `days` away from `base`, AT the given time of day.

    The seeder used to build these times as `base + timedelta(hours=hour,
    minutes=minute)`, which adds the intended hour to whatever time of day
    `base` happened to have (the seeding machine's clock), so every stored
    time was shifted by that clock reading and many fell outside the hours they
    were meant to be in. The time of day must be SET, not added.
    """
    return (base + timedelta(days=days)).replace(hour=hour, minute=minute, second=second, microsecond=0)


def load_validated_timestamps(csv_path=METADATA_CSV) -> dict:
    """call_id -> start time (naive IST) for every metadata.csv row whose
    timestamp is a complete, valid date-time. Missing or invalid rows are
    simply absent, never guessed."""
    path = Path(csv_path)
    if not path.is_file():
        return {}
    out = {}
    for row in load_csv_rows(path):
        call_id = (row.get("call_id") or "").strip()
        parsed = _try_parse_timestamp(row.get("start_time"))
        if call_id and parsed is not None and call_id not in out:
            out[call_id] = parsed
    return out



def confirm_reset():
    """Safety check before wiping database."""
    print("⚠️  WARNING: This will DELETE all existing data in the database!")
    print(f"   Database: {engine.url}")
    response = input("   Type 'yes' to continue: ")
    if response.lower() != "yes":
        print("   Aborted.")
        sys.exit(0)


def wipe_database():
    """Drop all tables and recreate schema."""
    print("🗑️  Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("📋 Creating fresh schema...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database reset complete.\n")


def seed_offices(db):
    """Create 3 offices as specified."""
    offices_data = [
        {"id": "o-01", "name": "Jogeshwari"},
        {"id": "o-02", "name": "Lower Parel"},
        {"id": "o-03", "name": "Bangalore"},
    ]
    
    offices = []
    for data in offices_data:
        office = Office(**data)
        db.add(office)
        offices.append(office)
    
    db.commit()
    print(f"✅ Created {len(offices)} offices")
    return offices


def seed_users(db, offices):
    """
    Create 68 users: 1 Head + 7 Managers + 60 Callers.
    Distribute across offices with realistic manager assignments.
    """
    users = []
    
    # 1 Head of Collections (no office restriction, sees all)
    head = AppUser(
        id="u-001",
        name="Rajesh Kumar",
        role="head",
        office_id=offices[0].id,  # Primary office: Jogeshwari
        manager_id=None,
    )
    db.add(head)
    users.append(head)
    
    # 7 Managers distributed across offices
    # Jogeshwari: 3 managers, Lower Parel: 2 managers, Bangalore: 2 managers
    managers_data = [
        {"id": "u-002", "name": "Priya Sharma", "office_id": offices[0].id},
        {"id": "u-003", "name": "Amit Desai", "office_id": offices[0].id},
        {"id": "u-004", "name": "Sneha Patel", "office_id": offices[0].id},
        {"id": "u-005", "name": "Vikram Singh", "office_id": offices[1].id},
        {"id": "u-006", "name": "Kavita Rao", "office_id": offices[1].id},
        {"id": "u-007", "name": "Suresh Nair", "office_id": offices[2].id},
        {"id": "u-008", "name": "Anjali Reddy", "office_id": offices[2].id},
    ]
    
    managers = []
    for i, data in enumerate(managers_data, start=2):
        manager = AppUser(
            id=data["id"],
            name=data["name"],
            role="manager",
            office_id=data["office_id"],
            manager_id=None,  # Managers report to head (not modeled in simplified schema)
        )
        db.add(manager)
        users.append(manager)
        managers.append(manager)
    
    # 60 Callers distributed across managers
    # Each manager gets 8-9 callers
    caller_names = [
        "Rahul", "Pooja", "Imran", "Neha", "Arjun", "Kavya", "Rohan", "Divya",
        "Karan", "Sanya", "Aditya", "Priyanka", "Varun", "Riya", "Siddharth", "Ananya",
        "Manish", "Shruti", "Akash", "Megha", "Nikhil", "Tanya", "Vishal", "Ishita",
        "Gaurav", "Nidhi", "Harsh", "Sakshi", "Ramesh", "Deepika", "Sunil", "Ayesha",
        "Naveen", "Ria", "Pankaj", "Swati", "Rajat", "Nisha", "Ashok", "Preeti",
        "Dinesh", "Rekha", "Prakash", "Simran", "Sandeep", "Meera", "Vijay", "Poorna",
        "Arun", "Kiran", "Manoj", "Lakshmi", "Sanjay", "Radha", "Ajay", "Sunita",
        "Ravi", "Madhuri", "Praveen", "Usha",
    ]
    
    caller_id_start = 9
    for i, name in enumerate(caller_names, start=caller_id_start):
        # Assign to manager round-robin
        manager = managers[i % len(managers)]
        
        caller = AppUser(
            id=f"u-{i:03d}",
            name=f"{name} {['Kumar', 'Singh', 'Sharma', 'Patel', 'Reddy', 'Nair'][i % 6]}",
            role="caller",
            office_id=manager.office_id,  # Caller's office matches their manager's
            manager_id=manager.id,
        )
        db.add(caller)
        users.append(caller)
    
    db.commit()
    print(f"✅ Created {len(users)} users (1 head, {len(managers)} managers, {len(caller_names)} callers)")
    return users, managers


def seed_rules(db):
    """Insert 6 fixed compliance rules."""
    rules = []
    for rule_data in RULES:
        rule = Rule(**rule_data)
        db.add(rule)
        rules.append(rule)
    
    db.commit()
    print(f"✅ Created {len(rules)} compliance rules (R1-R6)")
    return rules


def seed_metadata_calls(db, callers, offices):
    """
    Generate ~1,980 metadata-only call records.
    Distributed across callers with randomized but plausible values.
    """
    calls = []
    today = datetime.now()  # start times fall within the last 90 days
    
    languages = ["hi", "en", "mr", "kn", "hi-en"]  # Hindi, English, Marathi, Kannada, Mixed
    account_types = ["personal_loan", "credit_card"]
    
    # IDs CALL-0001..CALL-0020 are reserved for the 20 ground-truth calls
    # (seed_ground_truth_calls / ground_truth.json); start metadata-only
    # IDs after that range to avoid a primary-key collision.
    for i in range(1980):
        caller = random.choice(callers)

        # Random start time within permitted business hours (R1: 08:00-19:00)
        # over the last 90 days. The time of day is SET, never added to the
        # clock time of the machine running the seed.
        days_ago = random.randint(0, 90)
        hour = random.randint(*BUSINESS_HOURS)  # 08:00:00 - 18:59:59
        minute = random.randint(0, 59)

        start_time = at_time_of_day(today, -days_ago, hour, minute, random.randint(0, 59))

        call = Call(
            id=f"CALL-{i+21:04d}",
            caller_id=caller.id,
            office_id=caller.office_id,
            account_type=random.choice(account_types),
            start_time=start_time,
            duration_seconds=random.randint(30, 600),  # 30 sec to 10 min
            language=random.choice(languages),
            has_audio=False,
            audio_path=None,
            status="metadata_only",
            is_ground_truth=False,
            batch_id=None,
        )
        db.add(call)
        calls.append(call)
    
    db.commit()
    print(f"✅ Created {len(calls)} metadata-only call records")
    return calls


def seed_ground_truth_calls(db, callers, offices, metadata_csv=METADATA_CSV):
    """
    Create 20 call records for the audio files with ground truth labels.
    These are marked with has_audio=True, is_ground_truth=True, status='queued'.
    
    Call IDs match expected audio filenames: call_01.wav through call_20.wav.
    """
    ground_truth_calls = []
    
    # Specific scenarios for the first 5 calls (from call_scripts.md)
    specific_calls = [
        {
            "id": "CALL-0001",
            "caller_name": "Pooja Rane",
            "account_type": "personal_loan",
            "language": "hi",
            "start_time_offset": -30,  # 30 days ago, 10:30 AM (within hours)
            "hour": 10,
            "minute": 30,
            "duration": 120,
        },
        {
            "id": "CALL-0002",
            "caller_name": "Imran Khan",
            "account_type": "credit_card",
            "language": "hi-en",
            "start_time_offset": -25,  # 25 days ago, 3:15 PM
            "hour": 15,
            "minute": 15,
            "duration": 90,
        },
        {
            "id": "CALL-0003",
            "caller_name": "Anonymous Caller",  # Didn't introduce name properly
            "account_type": "personal_loan",
            "language": "en",
            "start_time_offset": -20,  # 20 days ago, 11:00 AM
            "hour": 11,
            "minute": 0,
            "duration": 75,
        },
        {
            "id": "CALL-0004",
            "caller_name": "Rekha Pillai",
            "account_type": "personal_loan",
            "language": "hi",
            "start_time_offset": -15,  # 15 days ago, 2:45 PM
            "hour": 14,
            "minute": 45,
            "duration": 110,
        },
        {
            "id": "CALL-0005",
            "caller_name": "Arjun Reddy",
            "account_type": "credit_card",
            "language": "en",
            "start_time_offset": -10,  # 10 days ago, 9:00 AM
            "hour": 9,
            "minute": 0,
            "duration": 135,
        },
    ]
    
    base_date = datetime.now()
    # Start times come from metadata.csv, the source of truth for the 20
    # role-play calls. The computed times below are only a fallback for a call
    # whose CSV row is missing or invalid.
    validated = load_validated_timestamps(metadata_csv)
    from_csv = 0

    for i, call_data in enumerate(specific_calls, start=1):
        # Find or create a caller with matching name pattern
        matching_caller = None
        for caller in callers:
            if call_data["caller_name"].split()[0] in caller.name:
                matching_caller = caller
                break
        
        if not matching_caller:
            matching_caller = random.choice(callers)
        
        start_time = validated.get(call_data["id"])
        if start_time is None:
            start_time = at_time_of_day(base_date, call_data["start_time_offset"], call_data["hour"], call_data["minute"])
        else:
            from_csv += 1
        
        call = Call(
            id=call_data["id"],
            caller_id=matching_caller.id,
            office_id=matching_caller.office_id,
            account_type=call_data["account_type"],
            start_time=start_time,
            duration_seconds=call_data["duration"],
            language=call_data["language"],
            has_audio=True,
            audio_path=f"data/audio/call_{i:02d}.wav",
            status="queued",  # Will be processed in Phase 6
            is_ground_truth=True,
            batch_id="ground_truth_batch",
        )
        db.add(call)
        ground_truth_calls.append(call)
    
    # Generate 15 more ground truth calls with varied scenarios
    # Including some with R1 violations (outside hours) and clean calls
    for i in range(6, 21):
        caller = random.choice(callers)
        
        # Mix of times including some R1 violations
        if i % 4 == 0:  # Every 4th call violates R1
            hour = random.choice([7, 19, 20, 21])  # Outside 8-19 range
        else:
            hour = random.randint(9, 17)  # Safe hours
        
        start_time = validated.get(f"CALL-{i:04d}")
        if start_time is None:
            start_time = at_time_of_day(base_date, -(50 - i * 2), hour, random.randint(0, 59))
        else:
            from_csv += 1
        
        call = Call(
            id=f"CALL-{i:04d}",
            caller_id=caller.id,
            office_id=caller.office_id,
            account_type=random.choice(["personal_loan", "credit_card"]),
            start_time=start_time,
            duration_seconds=random.randint(60, 180),
            language=random.choice(["hi", "en", "hi-en", "mr"]),
            has_audio=True,
            audio_path=f"data/audio/call_{i:02d}.wav",
            status="queued",
            is_ground_truth=True,
            batch_id="ground_truth_batch",
        )
        db.add(call)
        ground_truth_calls.append(call)
    
    db.commit()
    print(f"✅ Created {len(ground_truth_calls)} ground truth call records (with audio); {from_csv} start times from metadata.csv")
    return ground_truth_calls


def main():
    """Main seeding orchestration."""
    print("=" * 70)
    print("Speech Analytics & Compliance - Database Seeding")
    print("=" * 70)
    print()
    
    # Safety confirmation
    confirm_reset()
    
    # Wipe and recreate database
    wipe_database()
    
    # Create session
    db = SessionLocal()
    
    try:
        # Seed in dependency order
        print("📊 Seeding database...\n")
        
        offices = seed_offices(db)
        users, managers = seed_users(db, offices)
        
        # Get just callers for call generation
        callers = [u for u in users if u.role == "caller"]
        
        rules = seed_rules(db)
        metadata_calls = seed_metadata_calls(db, callers, offices)
        ground_truth_calls = seed_ground_truth_calls(db, callers, offices)
        
        print()
        print("=" * 70)
        print("✅ Database seeding complete!")
        print("=" * 70)
        print(f"   Offices: {len(offices)}")
        print(f"   Users: {len(users)} (1 head, {len(managers)} managers, {len(callers)} callers)")
        print(f"   Rules: {len(rules)}")
        print(f"   Metadata calls: {len(metadata_calls)}")
        print(f"   Ground truth calls: {len(ground_truth_calls)}")
        print(f"   Total calls: {len(metadata_calls) + len(ground_truth_calls)}")
        print("=" * 70)
        
    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
