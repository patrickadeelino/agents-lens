"""Public prompt request body."""

from typing import Annotated, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    StringConstraints,
    field_validator,
    model_validator,
)

from src.shared.executor_protocol import DEFAULT_AGENT, AgentName


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str

    @field_validator("prompt")
    @classmethod
    def require_text(cls, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must contain text.")
        return prompt


class NewPromptRequest(PromptRequest):
    agent: AgentName = DEFAULT_AGENT
    workspace_id: UUID | None = None
    workspace_run_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,128}$")] | None = None

    @model_validator(mode="after")
    def one_workspace_reference(self) -> Self:
        if self.workspace_id is not None and self.workspace_run_id is not None:
            raise ValueError("Provide one workspace reference.")
        return self
