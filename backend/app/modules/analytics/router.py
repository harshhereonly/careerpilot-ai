from fastapi import APIRouter

from app.modules.analytics.schemas import Summary
from app.modules.analytics.service import get_summary
from app.modules.auth.deps import CurrentUser, DbSession

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/summary", response_model=Summary)
def read_summary(user: CurrentUser, db: DbSession) -> Summary:
    return get_summary(db, user.id)
