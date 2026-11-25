"""NDJSON response messages exchanged with the host executor."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

SESSION_PERSISTENCE_HEADER = "X-Agent-Lens-Session-Persistence"


class WorkspaceSelection(BaseModel):
    id: UUID
    path: str


class ExecutorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    prompt: str
    thread_id: UUID | None = None
    workspace: str

    @field_validator("prompt")
    @classmethod
    def require_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Prompt must contain text.")
        return value


class RunOutput(BaseModel):
    stdout: str


class RunExit(BaseModel):
    exit_code: int | None


class RunError(BaseModel):
    error: Literal["event_too_large"]
