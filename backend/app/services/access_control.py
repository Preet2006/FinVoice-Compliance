"""
Single source of truth for "given this role + user id, which Call rows are
visible". Per MASTER_IMPLEMENTATION_PLAN §8.2 — every endpoint that returns
Call/Violation/analytics data must filter through this, so visibility can't
drift between screens.
"""

from typing import Optional

from sqlalchemy.orm import Session

from app.models import AppUser


def visible_caller_ids(role: str, active_user_id: str, db: Session) -> Optional[set]:
    """
    Returns the set of app_user.id values whose calls the given role/user
    may see, or None to mean "no restriction" (head of collections).

    - head:    None (sees all offices, all callers)
    - manager: the set of callers who report to active_user_id
    - caller:  just {active_user_id}
    """
    if role == "head":
        return None
    if role == "manager":
        return {
            u.id
            for u in db.query(AppUser.id).filter(AppUser.manager_id == active_user_id)
        }
    if role == "caller":
        return {active_user_id}
    raise ValueError(f"Unknown role: {role}")
