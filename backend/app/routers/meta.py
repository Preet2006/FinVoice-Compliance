"""
Metadata endpoints: offices and users, for filter/identity dropdowns.
Per MASTER_IMPLEMENTATION_PLAN §4.3.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import Office, AppUser
from app.schemas import OfficeOut, UserOut

router = APIRouter(prefix="/api/meta", tags=["meta"])


@router.get("/offices", response_model=List[OfficeOut])
def list_offices(_user=Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(Office).order_by(Office.name).all()


@router.get("/users", response_model=List[UserOut])
def list_users(
    role: Optional[str] = Query(default=None, pattern="^(caller|manager|head)$"),
    _user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(AppUser)
    if role:
        q = q.filter(AppUser.role == role)
    return q.order_by(AppUser.name).all()
