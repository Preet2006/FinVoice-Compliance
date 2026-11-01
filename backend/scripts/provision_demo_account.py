"""Create or reset one demo account without hardcoding or printing passwords.

Usage from backend:\n  python scripts/provision_demo_account.py --user-id u-head
The password is entered interactively and only its Argon2id hash is stored.
"""

import argparse
import getpass
import sys

from app.db import SessionLocal
from app.models import AppUser
from app.services.auth_service import hash_password


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    password = getpass.getpass("New password (minimum 12 characters): ")
    confirmation = getpass.getpass("Repeat password: ")
    if password != confirmation:
        print("Passwords do not match.", file=sys.stderr)
        return 2
    db = SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.id == args.user_id).first()
        if not user:
            print("Unknown user ID.", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        user.is_active = True
        db.commit()
        print(f"Account provisioned for {user.id} ({user.role}).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
