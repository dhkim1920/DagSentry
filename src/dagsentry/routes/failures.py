"""Failure Event ingestion route."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from dagsentry.db import get_session
from dagsentry.domain.failure_event import FailureEventCreate
from dagsentry.ingestion import ingest_failure_event
from dagsentry.metrics import record_counter
from dagsentry.security import verify_ingest_token

router = APIRouter(prefix="/api/v1", tags=["failures"])


class FailureIngestResponse(BaseModel):
    """Stable response for both new and duplicate Failure Events."""

    model_config = ConfigDict(extra="forbid")

    failure_event_id: UUID
    event_key: str
    created: bool


@router.post(
    "/failure-events",
    response_model=FailureIngestResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_ingest_token)],
)
def ingest_failure(
    event: FailureEventCreate,
    session: Annotated[Session, Depends(get_session)],
    request: Request,
) -> FailureIngestResponse:
    """Store one failed Task Try and enqueue diagnosis exactly once."""
    result = ingest_failure_event(session, event)
    record_counter(
        request.app.state.session_factory,
        "dagsentry_ingest_events_total",
        "created" if result.created else "duplicate",
    )
    return FailureIngestResponse(
        failure_event_id=result.failure_event_id,
        event_key=result.event_key,
        created=result.created,
    )
