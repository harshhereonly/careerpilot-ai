"""Second layer of tenant isolation, on top of row-level security.

RLS already hides other users' rows from an RLSSession. These helpers make the ownership rule
explicit in application code too, so a bug in either layer alone cannot expose another user's data.
Other users' rows are reported as 404, not 403, so their existence is not revealed.
"""

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.base import OwnedMixin


def owned_select[T: OwnedMixin](model: type[T], user_id: UUID) -> Select[T]:
    """Base query for list endpoints: only the caller's rows."""
    return select(model).where(model.user_id == user_id)


def get_owned_or_404[T: OwnedMixin](
    session: Session, model: type[T], object_id: UUID, user_id: UUID
) -> T:
    obj = session.scalars(owned_select(model, user_id).where(model.id == object_id)).one_or_none()
    if obj is None:
        raise NotFoundError(f"{model.__name__} not found.")
    return obj
