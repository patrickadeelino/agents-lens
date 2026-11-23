"""Expose pages and subscriptions for an existing capture."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse

from src.api.dependencies import RunIdPath, get_run_history, get_run_subscription
from src.api.sse import RunSubscriptionResponse
from src.application.run_history import (
    GetRunHistory,
    RunHistoryPage,
    RunReadError,
    RunReadErrorKind,
)
from src.application.run_subscription import SubscribeToRun

router = APIRouter()
RUN_READ_ERRORS: dict[RunReadErrorKind, tuple[int, str]] = {
    "run_not_found": (404, "Run was not found."),
    "trace_not_found": (404, "Run trace was not found."),
    "capture_starting": (409, "Run capture is starting."),
    "capture_unavailable": (503, "Run capture is unavailable."),
}


@router.get("/runs/{run_id}/events")
def run_events(
    run_id: RunIdPath,
    response: Response,
    history: Annotated[GetRunHistory, Depends(get_run_history)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> RunHistoryPage:
    """Return an ascending page and the latest observed state for its command items."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return history.execute(run_id, limit=limit, before=before)
    except RunReadError as error:
        status, detail = RUN_READ_ERRORS[error.kind]
        raise HTTPException(status_code=status, detail=detail) from error


@router.get(
    "/runs/{run_id}/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
def run_stream(
    run_id: RunIdPath,
    subscription: Annotated[SubscribeToRun, Depends(get_run_subscription)],
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[int | None, Header(ge=0)] = None,
) -> StreamingResponse:
    """Subscribe after an event ID, resuming from the browser's latest ID when supplied."""
    cursor = last_event_id if last_event_id is not None else after
    try:
        return RunSubscriptionResponse(subscription.execute(run_id, after=cursor))
    except RunReadError as error:
        status, detail = RUN_READ_ERRORS[error.kind]
        raise HTTPException(status_code=status, detail=detail) from error
