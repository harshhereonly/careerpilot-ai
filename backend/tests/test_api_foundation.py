"""Health checks, error handling, request context and CORS. No database, no credentials."""

import json
import logging

import pytest
import structlog
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError

from app.core.db import DatabaseNotConfiguredError
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.core.logging import REDACTED, configure_logging, redact_sensitive
from tests.conftest import ALLOWED_ORIGIN


class FakeDriverError(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("secret_table violates secret_constraint")
        self.sqlstate = sqlstate


class Signup(BaseModel):
    email: str
    age: int


@pytest.fixture
def failing_client(app: FastAPI) -> TestClient:
    """Routes that fail on purpose, one per branch of the centralized error handling."""
    router = APIRouter(prefix="/_test")

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    @router.get("/not-found")
    def not_found() -> None:
        raise NotFoundError("Contact not found.")

    @router.get("/conflict")
    def conflict() -> None:
        raise ConflictError()

    @router.get("/forbidden")
    def forbidden() -> None:
        raise PermissionDeniedError()

    @router.post("/signup")
    def signup(payload: Signup) -> dict[str, str]:
        return {"email": payload.email}

    @router.get("/db/unique")
    def db_unique() -> None:
        raise IntegrityError("INSERT secret", {}, FakeDriverError("23505"))

    @router.get("/db/rls")
    def db_rls() -> None:
        raise ProgrammingError("INSERT secret", {}, FakeDriverError("42501"))

    @router.get("/db/down")
    def db_down() -> None:
        raise OperationalError("SELECT secret", {}, FakeDriverError(None))

    @router.get("/db/other")
    def db_other() -> None:
        raise DBAPIError("SELECT secret", {}, FakeDriverError("42P01"))

    @router.get("/db/unconfigured")
    def db_unconfigured() -> None:
        raise DatabaseNotConfiguredError("DATABASE_URL is not set")

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


class TestHealth:
    def test_liveness(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_readiness_reports_unconfigured_database(self, client: TestClient) -> None:
        response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        assert response.json() == {
            "status": "unavailable",
            "checks": {"database": "not_configured"},
        }

    def test_readiness_hides_connection_details(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.config import get_settings
        from app.core.db import dispose_engine

        monkeypatch.setenv("DATABASE_URL", "postgresql://user:hunter2@127.0.0.1:1/nothing")
        get_settings.cache_clear()
        dispose_engine()

        response = client.get("/api/v1/health/ready")

        assert response.status_code == 503
        assert response.json()["checks"] == {"database": "error"}
        assert "hunter2" not in response.text
        assert "127.0.0.1" not in response.text

    def test_health_routes_need_no_token(self, client: TestClient) -> None:
        assert client.get("/api/v1/health").status_code != 401
        assert client.get("/api/v1/health/ready").status_code != 401


class TestErrors:
    def assert_error(self, response_json: dict[str, object], code: str) -> None:
        error = response_json["error"]
        assert isinstance(error, dict)
        assert error["code"] == code
        assert error["message"]
        assert error["request_id"]

    def test_unknown_route(self, client: TestClient) -> None:
        response = client.get("/api/v1/does-not-exist")

        assert response.status_code == 404
        self.assert_error(response.json(), "not_found")

    def test_wrong_method(self, client: TestClient) -> None:
        response = client.post("/api/v1/health")

        assert response.status_code == 405
        self.assert_error(response.json(), "method_not_allowed")

    def test_old_unversioned_health_route_is_gone(self, client: TestClient) -> None:
        assert client.get("/healthz").status_code == 404

    def test_unhandled_exception_never_leaks_its_message(self, failing_client: TestClient) -> None:
        response = failing_client.get("/_test/boom")

        assert response.status_code == 500
        self.assert_error(response.json(), "internal_error")
        assert "secret internal detail" not in response.text

    def test_unhandled_exception_is_logged_with_the_traceback(
        self, failing_client: TestClient
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            failing_client.get("/_test/boom")

        logged = [entry for entry in logs if entry["event"] == "unhandled_exception"]
        assert logged
        assert logged[0]["log_level"] == "error"
        assert logged[0]["exc_info"] is True

    @pytest.mark.parametrize(
        ("path", "status", "code"),
        [
            ("not-found", 404, "not_found"),
            ("conflict", 409, "conflict"),
            ("forbidden", 403, "forbidden"),
        ],
    )
    def test_app_errors_map_to_their_status(
        self, failing_client: TestClient, path: str, status: int, code: str
    ) -> None:
        response = failing_client.get(f"/_test/{path}")

        assert response.status_code == status
        self.assert_error(response.json(), code)

    def test_validation_error_has_details_but_not_the_submitted_values(
        self, failing_client: TestClient
    ) -> None:
        response = failing_client.post(
            "/_test/signup", json={"email": "ada@example.test", "age": "my-secret-value"}
        )

        assert response.status_code == 422
        self.assert_error(response.json(), "validation_error")
        details = response.json()["error"]["details"]
        assert details[0]["loc"] == ["body", "age"]
        assert set(details[0]) == {"loc", "msg", "type"}
        assert "my-secret-value" not in response.text

    @pytest.mark.parametrize(
        ("path", "status", "code"),
        [
            ("db/unique", 409, "conflict"),
            ("db/rls", 403, "forbidden"),
            ("db/down", 503, "database_unavailable"),
            ("db/other", 500, "internal_error"),
            ("db/unconfigured", 503, "database_unavailable"),
        ],
    )
    def test_database_errors_map_without_leaking_sql(
        self, failing_client: TestClient, path: str, status: int, code: str
    ) -> None:
        response = failing_client.get(f"/_test/{path}")

        assert response.status_code == status
        self.assert_error(response.json(), code)
        assert "secret" not in response.text


class TestRequestContext:
    def test_request_id_is_generated(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")

        assert len(response.headers["x-request-id"]) == 32

    def test_safe_incoming_request_id_is_kept(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-1234.abc_DEF"})

        assert response.headers["x-request-id"] == "trace-1234.abc_DEF"

    @pytest.mark.parametrize("bad_id", ["short", "has spaces in it", "x" * 65, "new\tline-attempt"])
    def test_unsafe_incoming_request_id_is_replaced(self, client: TestClient, bad_id: str) -> None:
        response = client.get("/api/v1/health", headers={"X-Request-ID": bad_id})

        assert response.headers["x-request-id"] != bad_id
        assert len(response.headers["x-request-id"]) == 32

    def test_error_body_and_header_share_the_request_id(self, client: TestClient) -> None:
        response = client.get("/api/v1/me")

        assert response.json()["error"]["request_id"] == response.headers["x-request-id"]

    def test_security_headers(self, client: TestClient) -> None:
        headers = client.get("/api/v1/health").headers

        assert headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer"
        assert headers["cache-control"] == "no-store"

    def test_requests_are_logged_without_query_strings(self, client: TestClient) -> None:
        with structlog.testing.capture_logs() as logs:
            client.get("/api/v1/me?token=secret-in-query")

        requests = [entry for entry in logs if entry["event"] == "request"]
        assert requests[0]["path"] == "/api/v1/me"
        assert requests[0]["status"] == 401
        assert requests[0]["method"] == "GET"
        assert "secret-in-query" not in repr(logs)

    def test_context_is_not_shared_between_requests(self, client: TestClient) -> None:
        first = client.get("/api/v1/health").headers["x-request-id"]
        second = client.get("/api/v1/health").headers["x-request-id"]

        assert first != second


class TestCors:
    def test_allowed_origin(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"Origin": ALLOWED_ORIGIN})

        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

    def test_other_origins_get_no_cors_headers(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"Origin": "https://evil.example.test"})

        assert "access-control-allow-origin" not in response.headers

    def test_credentials_are_not_allowed(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"Origin": ALLOWED_ORIGIN})

        assert "access-control-allow-credentials" not in response.headers

    def test_preflight_allows_the_authorization_header(self, client: TestClient) -> None:
        response = client.options(
            "/api/v1/me",
            headers={
                "Origin": ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "Authorization",
            },
        )

        assert response.status_code == 200
        assert "authorization" in response.headers["access-control-allow-headers"].lower()

    def test_preflight_from_another_origin_is_refused(self, client: TestClient) -> None:
        response = client.options(
            "/api/v1/me",
            headers={
                "Origin": "https://evil.example.test",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 400

    def test_error_responses_carry_cors_headers(self, client: TestClient) -> None:
        response = client.get("/api/v1/me", headers={"Origin": ALLOWED_ORIGIN})

        assert response.status_code == 401
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN

    def test_unhandled_500s_carry_cors_headers_and_a_request_id(
        self, failing_client: TestClient
    ) -> None:
        """Otherwise browsers report a CORS failure instead of the real error."""
        response = failing_client.get("/_test/boom", headers={"Origin": ALLOWED_ORIGIN})

        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
        assert response.headers["x-request-id"]


class TestLogging:
    @pytest.mark.parametrize(
        "field",
        ["password", "api_key", "authorization", "access_token", "client_secret", "database_url"],
    )
    def test_sensitive_fields_are_redacted(self, field: str) -> None:
        result = redact_sensitive(None, "info", {"event": "x", field: "hunter2"})

        assert result[field] == REDACTED

    def test_ordinary_fields_are_kept(self) -> None:
        result = redact_sensitive(None, "info", {"event": "x", "user_id": "abc", "status": 200})

        assert result == {"event": "x", "user_id": "abc", "status": 200}

    def test_json_mode_emits_one_parseable_object_per_line(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging("INFO", json_output=True)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id="req-123456")

        structlog.get_logger("test.structured").info("hello", password="hunter2", status=200)
        logging.getLogger("test.stdlib").warning("from the standard library")

        structlog.contextvars.clear_contextvars()
        lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
        first, second = (json.loads(line) for line in lines)
        configure_logging()

        assert first["event"] == "hello"
        assert first["level"] == "info"
        assert first["status"] == 200
        assert first["request_id"] == "req-123456"
        assert first["password"] == REDACTED
        assert first["timestamp"].endswith("Z")
        assert second["event"] == "from the standard library"
        assert second["level"] == "warning"
