from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import RoleContext, get_role_context
from app.schemas import DashboardOut
from app.services import dashboard_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardOut)
def get_dashboard(ctx: RoleContext = Depends(get_role_context), db: Session = Depends(get_db)):
    return dashboard_service.build_dashboard(db, ctx.role, ctx.user_id)
