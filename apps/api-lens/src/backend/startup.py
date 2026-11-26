"""Prepare the saved history before the API accepts requests."""

from src.backend.api import dependencies
from src.backend.application.capture.interrupted_runs import close_interrupted_runs


def recover_history() -> None:
    close_interrupted_runs(dependencies.conversation_store().saved_runs())
