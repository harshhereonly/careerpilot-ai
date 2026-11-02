from fastapi import APIRouter

from app.core.errors import NotFoundError
from app.models import User
from app.modules.auth.deps import CurrentUser, DbSession
from app.modules.auth.schemas import UserRead

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=UserRead)
def read_me(user: CurrentUser, db: DbSession) -> User:
    """The caller's own row, read through row-level security (a full auth -> RLS round trip)."""
    row = db.get(User, user.id)
    if row is None:
        raise NotFoundError("User not found.")
    return row
