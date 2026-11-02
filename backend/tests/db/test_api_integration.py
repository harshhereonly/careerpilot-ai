"""The whole path: HTTP -> token -> provisioning -> RLS session -> Postgres -> error mapping."""

from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest
from fastapi import APIRouter, FastAPI, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.ownership import get_owned_or_404, owned_select
from app.models import Contact, User
from app.modules.auth.deps import CurrentUser, DbSession
from app.modules.auth.service import clear_provisioning_cache
from tests.helpers import SigningKey, bearer, make_claims, sign

TokenFactory = Callable[..., str]


class ContactIn(BaseModel):
    full_name: str
    email: str | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    full_name: str


def _contacts_router() -> APIRouter:
    """Stand-in for a future module, built only from the real dependencies and helpers."""
    router = APIRouter(prefix="/_test/contacts")

    @router.post("", status_code=status.HTTP_201_CREATED, response_model=ContactOut)
    def create(payload: ContactIn, user: CurrentUser, db: DbSession) -> Contact:
        row = Contact(user_id=user.id, full_name=payload.full_name, email=payload.email)
        db.add(row)
        db.commit()
        return row

    @router.get("", response_model=list[ContactOut])
    def list_contacts(user: CurrentUser, db: DbSession) -> list[Contact]:
        return list(db.scalars(owned_select(Contact, user.id)).all())

    @router.get("/{contact_id}", response_model=ContactOut)
    def read(contact_id: UUID, user: CurrentUser, db: DbSession) -> Contact:
        return get_owned_or_404(db, Contact, contact_id, user.id)

    @router.get("/unscoped/count")
    def count_everything(_user: CurrentUser, db: DbSession) -> dict[str, int]:
        """A query that forgot to filter by owner: only RLS stands between it and other users."""
        return {"count": db.scalar(select(func.count()).select_from(Contact)) or 0}

    @router.post("/buggy/as/{owner_id}", status_code=status.HTTP_201_CREATED)
    def buggy_create(owner_id: UUID, _user: CurrentUser, db: DbSession) -> None:
        """Simulates a coding mistake that trusts a client-supplied owner."""
        db.add(Contact(user_id=owner_id, full_name="Planted"))
        db.commit()

    return router


@pytest.fixture
def db_client(
    app: FastAPI, test_db_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    get_settings.cache_clear()
    dispose_engine()
    app.include_router(_contacts_router())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def token_for(signing_key: SigningKey, engine: Engine) -> Iterator[TokenFactory]:
    seen: set[UUID] = set()

    def factory(user_id: UUID | None = None, email: str | None = None) -> str:
        subject = user_id or uuid4()
        seen.add(subject)
        return sign(make_claims(subject, email=email), signing_key)

    yield factory

    if seen:
        with Session(engine) as session:
            session.execute(delete(User).where(User.id.in_(seen)))
            session.commit()


def test_readiness_reports_a_healthy_database(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok"}}


def test_first_request_provisions_the_user_and_me_returns_them(
    db_client: TestClient, token_for: TokenFactory, engine: Engine
) -> None:
    user_id = uuid4()
    email = f"{user_id}@example.test"

    response = db_client.get("/api/v1/me", headers=bearer(token_for(user_id, email)))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(user_id)
    assert body["email"] == email
    with Session(engine) as session:
        assert session.scalar(select(User.email).where(User.id == user_id)) == email


def test_repeated_requests_do_not_duplicate_or_fail(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    token = token_for()

    first = db_client.get("/api/v1/me", headers=bearer(token))
    clear_provisioning_cache()
    second = db_client.get("/api/v1/me", headers=bearer(token))

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]


def test_email_change_in_supabase_reaches_our_copy(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    user_id = uuid4()
    db_client.get("/api/v1/me", headers=bearer(token_for(user_id, f"old-{user_id}@example.test")))
    clear_provisioning_cache()

    new_email = f"new-{user_id}@example.test"
    response = db_client.get("/api/v1/me", headers=bearer(token_for(user_id, new_email)))

    assert response.json()["email"] == new_email


def test_each_user_gets_only_themselves_from_me(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    alice, bob = uuid4(), uuid4()

    alices = db_client.get("/api/v1/me", headers=bearer(token_for(alice)))
    bobs = db_client.get("/api/v1/me", headers=bearer(token_for(bob)))

    assert alices.json()["id"] == str(alice)
    assert bobs.json()["id"] == str(bob)


def test_owner_is_taken_from_the_token_not_the_body(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    alice, bob = uuid4(), uuid4()

    response = db_client.post(
        "/_test/contacts",
        headers=bearer(token_for(alice)),
        json={"full_name": "Grace", "user_id": str(bob), "owner_id": str(bob)},
    )

    assert response.status_code == 201
    assert response.json()["user_id"] == str(alice)


def test_other_users_record_is_a_404_not_a_403(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    alice, bob = token_for(), token_for()
    created = db_client.post("/_test/contacts", headers=bearer(alice), json={"full_name": "Grace"})
    contact_id = created.json()["id"]

    owner_view = db_client.get(f"/_test/contacts/{contact_id}", headers=bearer(alice))
    other_view = db_client.get(f"/_test/contacts/{contact_id}", headers=bearer(bob))

    assert owner_view.status_code == 200
    assert other_view.status_code == 404
    assert other_view.json()["error"]["code"] == "not_found"


def test_lists_only_contain_your_own_records(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    alice, bob = token_for(), token_for()
    db_client.post("/_test/contacts", headers=bearer(alice), json={"full_name": "A"})
    db_client.post("/_test/contacts", headers=bearer(bob), json={"full_name": "B"})

    names = [
        row["full_name"] for row in db_client.get("/_test/contacts", headers=bearer(alice)).json()
    ]

    assert names == ["A"]


def test_rls_protects_even_a_query_that_forgot_to_filter(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    alice, bob = token_for(), token_for()
    db_client.post("/_test/contacts", headers=bearer(alice), json={"full_name": "A"})
    db_client.post("/_test/contacts", headers=bearer(alice), json={"full_name": "B"})

    alices = db_client.get("/_test/contacts/unscoped/count", headers=bearer(alice))
    bobs = db_client.get("/_test/contacts/unscoped/count", headers=bearer(bob))

    assert alices.json() == {"count": 2}
    assert bobs.json() == {"count": 0}


def test_buggy_endpoint_trusting_a_client_owner_is_stopped_by_the_database(
    db_client: TestClient, token_for: TokenFactory, engine: Engine
) -> None:
    alice_id, bob_id = uuid4(), uuid4()
    alice, bob = token_for(alice_id), token_for(bob_id)
    db_client.get("/api/v1/me", headers=bearer(bob))

    response = db_client.post(f"/_test/contacts/buggy/as/{bob_id}", headers=bearer(alice))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    with Session(engine) as session:
        planted = session.scalar(
            select(func.count()).select_from(Contact).where(Contact.user_id == bob_id)
        )
    assert planted == 0


def test_duplicate_is_a_409_without_leaking_the_constraint_name(
    db_client: TestClient, token_for: TokenFactory
) -> None:
    alice = token_for()
    payload = {"full_name": "Grace", "email": f"{uuid4()}@example.test"}
    db_client.post("/_test/contacts", headers=bearer(alice), json=payload)

    response = db_client.post("/_test/contacts", headers=bearer(alice), json=payload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert "uq_contacts" not in response.text
    assert "contacts" not in response.text


def test_expired_token_never_reaches_the_database(
    db_client: TestClient, signing_key: SigningKey, engine: Engine
) -> None:
    user_id = uuid4()
    token = sign(make_claims(user_id, expires_in=-60), signing_key)

    response = db_client.get("/api/v1/me", headers=bearer(token))

    assert response.status_code == 401
    with Session(engine) as session:
        assert session.scalar(select(User.id).where(User.id == user_id)) is None
