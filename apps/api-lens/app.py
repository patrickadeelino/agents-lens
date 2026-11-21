"""Serve a recorded Agent Lens trace as a server-sent event stream."""

import asyncio
from collections.abc import AsyncIterator
from itertools import pairwise
from pathlib import Path
from time import monotonic_ns
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from src.trace_events import TimedEvent
from src.trace_reader import read_timed_trace

PROJECT_DIR = Path(__file__).resolve().parents[2]
REPLAY_TRACE = PROJECT_DIR / "examples" / "traces" / "readme-run.timed.sanitized.jsonl"

app = FastAPI(title="Agent Lens")


def get_replay_events() -> tuple[TimedEvent, ...]:
    """Load and validate the fixed, sanitized replay before opening the stream."""
    try:
        with REPLAY_TRACE.open(encoding="utf-8") as trace:
            records = tuple(read_timed_trace(trace))
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Replay trace is unavailable.") from error

    if not records or any(
        current.elapsed_ns > following.elapsed_ns
        for current, following in pairwise(records)
    ):
        raise HTTPException(status_code=503, detail="Replay trace has invalid arrival offsets.")
    return records


async def replay_stream(records: tuple[TimedEvent, ...]) -> AsyncIterator[str]:
    """Send the first event immediately and preserve subsequent arrival intervals."""
    first_offset_ns = records[0].elapsed_ns
    started_at_ns = monotonic_ns()

    for record in records:
        target_ns = started_at_ns + record.elapsed_ns - first_offset_ns
        remaining_ns = max(0, target_ns - monotonic_ns())
        await asyncio.sleep(remaining_ns / 1_000_000_000)
        yield f"event: record\ndata: {record.model_dump_json()}\n\n"

    yield "event: end\ndata: {}\n\n"


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(PROJECT_DIR / "apps" / "web" / "index.html", media_type="text/html")


@app.get(
    "/events",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def events(
    records: Annotated[tuple[TimedEvent, ...], Depends(get_replay_events)],
) -> StreamingResponse:
    return StreamingResponse(
        replay_stream(records),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )
