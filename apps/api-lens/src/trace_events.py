"""Shared models for a Codex event and its observed arrival offset."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="allow", hide_input_in_errors=True)

    type: str


class TimedEvent(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    elapsed_ns: Annotated[int, Field(ge=0)]
    event: TraceEvent
