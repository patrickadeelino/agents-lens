"""Read from a turn's events how it ended and what the agent answered."""

import re
from typing import Literal

from src.shared.trace_events import TraceEvent

type TurnStatus = Literal["completed", "failed", "unconfirmed"]

_BOUNDARIES = frozenset({"turn.started", "turn.completed", "turn.failed"})
_ONLY_REDACTED = re.compile(r"\s*\[redacted:[^\]]+\]\s*", re.IGNORECASE)


def turn_boundary(event: TraceEvent) -> str | None:
    """Return the event type when it marks the start or the end of a turn."""
    return event.type if event.type in _BOUNDARIES else None


def agent_reply(event: TraceEvent) -> str | None:
    """Return the text of a finished agent message that shows something to the person."""
    item = (event.model_extra or {}).get("item")
    if event.type != "item.completed" or not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    if not isinstance(text, str) or not text.strip() or _ONLY_REDACTED.fullmatch(text):
        return None
    return text


def turn_status(last_boundary: str | None, exit_code: int | None, *, capture_failed: bool = False) -> TurnStatus:
    """Report success only when the turn completed and the process confirmed it."""
    if capture_failed or last_boundary == "turn.failed" or (exit_code is not None and exit_code != 0):
        return "failed"
    if last_boundary == "turn.completed" and exit_code == 0:
        return "completed"
    return "unconfirmed"
