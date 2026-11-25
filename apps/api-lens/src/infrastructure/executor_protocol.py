"""NDJSON response messages exchanged with the host executor."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SESSION_PERSISTENCE_HEADER = "X-Agent-Lens-Session-Persistence"
MAX_FOLDER_PATH = 4096


class WorkspaceSelection(BaseModel):
    id: UUID
    path: str


class FolderRequest(BaseModel):
    """A folder named relative to the workspace root; an empty path is the root."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    path: str = Field(max_length=MAX_FOLDER_PATH)


class FolderListing(BaseModel):
    root: str
    path: str
    folders: list[str]
    truncated: bool


class WorkspaceCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    workspace: str


class ExecutorRequest(WorkspaceCheck):
    prompt: str
    thread_id: UUID | None = None

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
