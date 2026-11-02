"""Map database failures to client-safe responses (no SQL, table or constraint names)."""

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

from app.core.db import DatabaseNotConfiguredError
from app.core.errors import error_response

log = structlog.get_logger(__name__)

_INSUFFICIENT_PRIVILEGE = "42501"  # also what a row-level-security violation reports


async def _handle_not_configured(_request: Request, exc: Exception) -> JSONResponse:
    log.error("database_not_configured", detail=str(exc))
    return error_response(503, "database_unavailable", "The database is not available.")


async def _handle_dbapi_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DBAPIError)
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if isinstance(exc, OperationalError | InterfaceError) or exc.connection_invalidated:
        log.error("database_unreachable", error=type(exc.orig).__name__)
        return error_response(503, "database_unavailable", "The database is not available.")
    if sqlstate == _INSUFFICIENT_PRIVILEGE:
        log.error("database_permission_denied", sqlstate=sqlstate)
        return error_response(403, "forbidden", "You do not have permission to do that.")
    if isinstance(sqlstate, str) and sqlstate.startswith("23"):
        log.warning("database_integrity_violation", sqlstate=sqlstate)
        return error_response(
            409, "conflict", "The request conflicts with existing data or violates a rule."
        )
    log.error("database_error", sqlstate=sqlstate, error=type(exc.orig).__name__)
    return error_response(500, "internal_error", "An unexpected error occurred.")


def register_db_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DatabaseNotConfiguredError, _handle_not_configured)
    app.add_exception_handler(DBAPIError, _handle_dbapi_error)
