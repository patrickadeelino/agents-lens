"""NDJSON response messages exchanged with the host executor."""

from typing import Literal

from pydantic import BaseModel


class RunOutput(BaseModel):
    stdout: str


class RunExit(BaseModel):
    exit_code: int | None


class RunError(BaseModel):
    error: Literal["event_too_large"]
