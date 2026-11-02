from typing import Literal

import structlog
from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import database_configured, system_session

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


class Liveness(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ok", "unavailable"]
    checks: dict[str, str]


@router.get("", response_model=Liveness)
def liveness() -> Liveness:
    """Process is up. Does not touch any dependency, so it is safe for frequent probes."""
    return Liveness(status="ok")


@router.get("/ready", response_model=Readiness, responses={503: {"model": Readiness}})
def readiness(response: Response) -> Readiness:
    """Dependencies are reachable. Reports a status per check, never the error text."""
    checks = {"database": _check_database()}
    healthy = all(result == "ok" for result in checks.values())
    if not healthy:
        response.status_code = 503
    return Readiness(status="ok" if healthy else "unavailable", checks=checks)


def _check_database() -> str:
    if not database_configured():
        return "not_configured"
    try:
        with system_session() as session:
            session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        log.error("readiness_database_failed", error=type(exc).__name__)
        return "error"
    return "ok"
