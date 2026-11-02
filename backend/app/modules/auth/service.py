import threading
import time
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert

from app.core.db import system_session
from app.models import User
from app.modules.auth.tokens import AuthenticatedUser

_TTL_SECONDS = 300.0
_MAX_CACHED = 10_000
_lock = threading.Lock()
_recently_provisioned: dict[UUID, float] = {}


def ensure_user_exists(user: AuthenticatedUser) -> None:
    """Create or refresh the app-level ``users`` row for a verified Supabase user.

    Runs on the privileged connection because the ``authenticated`` role may not insert into
    ``users``. It only ever touches the row whose id came from the verified token. A short
    in-process cache avoids a write on every request.
    """
    now = time.monotonic()
    with _lock:
        seen = _recently_provisioned.get(user.id)
        if seen is not None and now - seen < _TTL_SECONDS:
            return

    statement = insert(User).values(id=user.id, email=user.email)
    statement = statement.on_conflict_do_update(
        index_elements=[User.id],
        set_={"email": user.email},
        where=User.email.is_distinct_from(user.email),
    )
    with system_session() as session:
        session.execute(statement)
        session.commit()

    with _lock:
        if len(_recently_provisioned) >= _MAX_CACHED:
            _recently_provisioned.clear()
        _recently_provisioned[user.id] = now


def clear_provisioning_cache() -> None:
    with _lock:
        _recently_provisioned.clear()
