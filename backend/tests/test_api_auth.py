"""Authentication through the real HTTP stack, with locally signed tokens and no database."""

import re
from typing import Any
from uuid import uuid4

import jwt
import pytest
import structlog
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.modules.auth.deps import CurrentUser, get_token_verifier
from app.modules.auth.tokens import TokenVerifier
from tests.helpers import AUDIENCE, HS256_SECRET, SigningKey, bearer, make_claims, sign

# Routes that are public on purpose. Adding one here is a security decision.
PUBLIC_ROUTES = {"/api/v1/health", "/api/v1/health/ready"}


@pytest.fixture
def whoami_client(app: FastAPI) -> TestClient:
    """A protected route that needs no database, so the success path can be tested in isolation."""
    router = APIRouter()

    @router.get("/_test/whoami")
    def whoami(user: CurrentUser) -> dict[str, str]:
        return {"id": str(user.id), "email": user.email}

    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def test_valid_token_reaches_the_route(whoami_client: TestClient, signing_key: SigningKey) -> None:
    user_id = uuid4()
    token = sign(make_claims(user_id, email="ada@example.test"), signing_key)

    response = whoami_client.get("/_test/whoami", headers=bearer(token))

    assert response.status_code == 200
    assert response.json() == {"id": str(user_id), "email": "ada@example.test"}


def test_missing_token_is_401(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "token abc", "abc", "Bearer  "])
def test_malformed_authorization_header_is_401(client: TestClient, header: str) -> None:
    response = client.get("/api/v1/me", headers={"Authorization": header})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_invalid_token_is_401_without_detail(client: TestClient) -> None:
    response = client.get("/api/v1/me", headers=bearer("not-a-jwt"))

    assert response.status_code == 401
    body = response.json()["error"]
    assert body["code"] == "invalid_token"
    assert body["message"] == "Invalid access token."
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_expired_token_has_its_own_code_so_clients_can_refresh(
    client: TestClient, signing_key: SigningKey
) -> None:
    response = client.get(
        "/api/v1/me", headers=bearer(sign(make_claims(expires_in=-60), signing_key))
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "token_expired"


def test_token_for_the_wrong_audience_is_401(client: TestClient, signing_key: SigningKey) -> None:
    token = sign(make_claims(aud="some-other-api"), signing_key)

    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_service_role_token_is_401(client: TestClient, signing_key: SigningKey) -> None:
    token = sign(make_claims(role="service_role"), signing_key)

    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_unsigned_token_is_401(client: TestClient) -> None:
    token = jwt.encode(make_claims(), key="", algorithm="none")

    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_shared_secret_token_is_401_when_not_configured(client: TestClient) -> None:
    token = jwt.encode(make_claims(), HS256_SECRET, algorithm="HS256")

    assert client.get("/api/v1/me", headers=bearer(token)).status_code == 401


def test_unconfigured_auth_is_503_not_401(app: FastAPI, signing_key: SigningKey) -> None:
    """A server problem must not look like a bad credential, or clients would log users out."""
    unconfigured = TokenVerifier(audience=AUDIENCE, issuer=None, jwks=None, hs256_secret=None)
    app.dependency_overrides[get_token_verifier] = lambda: unconfigured

    response = TestClient(app).get("/api/v1/me", headers=bearer(sign(make_claims(), signing_key)))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_unavailable"


def test_tokens_never_reach_the_logs(client: TestClient, signing_key: SigningKey) -> None:
    good_looking = sign(make_claims(expires_in=-60), signing_key)

    with structlog.testing.capture_logs() as logs:
        client.get("/api/v1/me", headers=bearer(good_looking))
        client.get("/api/v1/me", headers=bearer("not-a-jwt-but-still-secret"))

    assert logs, "expected the rejections to be logged"
    rendered = repr(logs)
    assert good_looking not in rendered
    assert "not-a-jwt-but-still-secret" not in rendered


def test_rejection_is_logged_with_a_reason(client: TestClient, signing_key: SigningKey) -> None:
    with structlog.testing.capture_logs() as logs:
        client.get("/api/v1/me", headers=bearer(sign(make_claims(expires_in=-60), signing_key)))

    rejected = [entry for entry in logs if entry["event"] == "auth_rejected"]
    assert rejected
    assert rejected[0]["code"] == "token_expired"


def test_every_api_route_requires_authentication_unless_listed_as_public(
    app: FastAPI, client: TestClient
) -> None:
    """Guards all future endpoints: an endpoint that forgets CurrentUser fails this test.

    Behavioural on purpose. It calls each documented operation without a token instead of walking
    FastAPI's route objects, whose structure is private and has already changed once.
    """
    protected = [
        (method, path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
        if path.startswith("/api/") and path not in PUBLIC_ROUTES
    ]
    assert ("get", "/api/v1/me") in protected  # the check must not pass by finding nothing

    for method, path in protected:
        url = re.sub(r"\{[^}]+\}", str(uuid4()), path)
        response = client.request(method.upper(), url, json={})
        assert response.status_code == 401, f"{method.upper()} {path} is not protected"


def test_public_route_list_is_not_stale(app: FastAPI) -> None:
    assert set(app.openapi()["paths"]) >= PUBLIC_ROUTES


FORBIDDEN_INPUTS = {"user_id", "userId", "owner_id", "ownerId"}


def _client_supplied_user_ids(schema: dict[str, Any]) -> list[str]:
    """Every request parameter or body property through which a client could claim an identity."""
    found: list[str] = []
    components = schema.get("components", {}).get("schemas", {})
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            where = f"{method.upper()} {path}"
            for parameter in operation.get("parameters", []):
                if parameter["name"] in FORBIDDEN_INPUTS:
                    found.append(f"{where} parameter {parameter['name']}")
            for media in operation.get("requestBody", {}).get("content", {}).values():
                body = media.get("schema", {})
                if "$ref" in body:
                    body = components[body["$ref"].rsplit("/", 1)[-1]]
                for name in body.get("properties", {}):
                    if name in FORBIDDEN_INPUTS:
                        found.append(f"{where} body property {name}")
    return found


def test_no_endpoint_accepts_a_user_id_from_the_client(app: FastAPI) -> None:
    """Identity comes from the verified token only. Guards all future endpoints."""
    assert _client_supplied_user_ids(app.openapi()) == []


def test_user_id_detector_really_detects() -> None:
    class Payload(BaseModel):
        user_id: str

    offending = FastAPI()

    @offending.post("/in-body")
    def in_body(payload: Payload) -> None:
        return None

    @offending.get("/in-query")
    def in_query(user_id: str) -> None:
        return None

    assert _client_supplied_user_ids(offending.openapi()) == [
        "POST /in-body body property user_id",
        "GET /in-query parameter user_id",
    ]
