import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_response

log = structlog.get_logger("app.request")

REQUEST_ID_HEADER = "X-Request-ID"
# Only accept a caller-supplied id that cannot be used to forge log lines or headers.
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    # API responses carry per-user data; never let a shared cache keep them.
    "Cache-Control": "no-store",
}
_QUIET_PATH_PREFIX = "/api/v1/health"


def _incoming_request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            candidate: str = value.decode("latin-1")
            if _SAFE_REQUEST_ID.match(candidate):
                return candidate
    return uuid.uuid4().hex


class RequestContextMiddleware:
    """Request id, security headers, access log and a safe 500 for anything unhandled.

    Pure ASGI (not BaseHTTPMiddleware) so contextvars bound by dependencies, such as the
    authenticated user id, are still visible when the access line is written.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = request_id
                for name, value in _SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_headers)
            except Exception:
                log.exception("unhandled_exception", method=scope["method"], path=scope["path"])
                if response_started:
                    raise
                response = error_response(500, "internal_error", "An unexpected error occurred.")
                await response(scope, receive, send_with_headers)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 1)
            path = scope["path"]
            quiet = path.startswith(_QUIET_PATH_PREFIX) and status_code < 400
            emit = log.debug if quiet else (log.error if status_code >= 500 else log.info)
            emit(
                "request",
                method=scope["method"],
                path=path,
                status=status_code,
                duration_ms=duration_ms,
            )
