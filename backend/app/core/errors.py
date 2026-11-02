"""One error shape for the whole API.

Every error response is ``{"error": {"code", "message", "request_id", "details"?}}``. Clients
branch on ``code``; ``message`` is for humans. Unexpected exceptions never leak their text.
"""

from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger(__name__)

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


class AppError(Exception):
    """Base class for errors that are safe to show to the client."""

    status_code: int = 500
    code: str = "internal_error"
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        self.headers = dict(headers) if headers else {}
        super().__init__(self.message)


class AuthenticationError(AppError):
    status_code = 401
    code = "unauthenticated"
    default_message = "Authentication required."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        merged = {"WWW-Authenticate": "Bearer"}
        merged.update(headers or {})
        super().__init__(message, code=code, headers=merged)


class PermissionDeniedError(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to do that."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    default_message = "The request conflicts with the current state."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    default_message = "The service is temporarily unavailable."


def current_request_id() -> str | None:
    value = structlog.contextvars.get_contextvars().get("request_id")
    return value if isinstance(value, str) else None


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: Sequence[Mapping[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "error": {"code": code, "message": message, "request_id": current_request_id()}
    }
    if details:
        body["error"]["details"] = list(details)
    return JSONResponse(body, status_code=status_code, headers=dict(headers) if headers else None)


async def _handle_app_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    if exc.status_code >= 500:
        log.error("app_error", code=exc.code, status=exc.status_code, message=exc.message)
    return error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)


async def _handle_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Report where and why, never the submitted value: it may be a password or a token.
    details = [
        {"loc": jsonable_encoder(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed.", details=details)


async def _handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _HTTP_CODES.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    if exc.status_code >= 500:
        log.error("http_exception", status=exc.status_code, detail=message)
    return error_response(exc.status_code, code, message, headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    """Unhandled exceptions are caught in RequestContextMiddleware so CORS headers still apply."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
