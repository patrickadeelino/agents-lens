"""Prompt request body shared by the web API and the host executor."""

from pydantic import BaseModel, ConfigDict, field_validator


class PromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str

    @field_validator("prompt")
    @classmethod
    def require_text(cls, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must contain text.")
        return prompt
