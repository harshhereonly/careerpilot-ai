from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

import structlog
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.db import open_rls_session
from app.core.errors import AuthenticationError, ServiceUnavailableError
from app.modules.auth.service import ensure_user_exists
from app.modules.auth.tokens import (
    AuthenticatedUser,
    AuthUnavailableError,
    TokenError,
    TokenVerifier,
    build_token_verifier,
)

log = structlog.get_logger(__name__)

# auto_error=False so a missing or malformed header produces our own 401 body, not FastAPI's.
bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")


@lru_cache
def get_token_verifier() -> TokenVerifier:
    return build_token_verifier(get_settings())


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    verifier: Annotated[TokenVerifier, Depends(get_token_verifier)],
) -> AuthenticatedUser:
    """The one place a request becomes an identity. Never read a user id from the request body."""
    if credentials is None:
        raise AuthenticationError("Authentication required.")
    try:
        # Key refreshes do blocking network I/O, so keep verification off the event loop.
        user = await run_in_threadpool(verifier.verify_user, credentials.credentials)
    except TokenError as exc:
        log.info("auth_rejected", code=exc.code, reason=exc.reason)
        message = (
            "The access token has expired."
            if exc.code == "token_expired"
            else "Invalid access token."
        )
        raise AuthenticationError(
            message,
            code=exc.code,
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        ) from exc
    except AuthUnavailableError as exc:
        log.error("auth_unavailable", detail=str(exc))
        raise ServiceUnavailableError(
            "Authentication is temporarily unavailable.", code="auth_unavailable"
        ) from exc
    structlog.contextvars.bind_contextvars(user_id=str(user.id))
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def get_db(user: CurrentUser) -> Iterator[Session]:
    """A session scoped to the caller by row-level security. Commit explicitly to persist."""
    ensure_user_exists(user)
    session = open_rls_session(user.claims)
    try:
        yield session
    finally:
        session.close()


DbSession = Annotated[Session, Depends(get_db)]
