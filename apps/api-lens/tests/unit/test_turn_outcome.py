import pytest

from src.domain.trace_events import TraceEvent
from src.domain.turn_outcome import agent_reply, turn_boundary, turn_status


@pytest.mark.parametrize(("boundary", "exit_code", "capture_failed", "status"), [
    ("turn.completed", 0, False, "completed"),
    ("turn.completed", None, False, "unconfirmed"),
    ("turn.started", 0, False, "unconfirmed"),
    (None, None, False, "unconfirmed"),
    ("turn.failed", 0, False, "failed"),
    ("turn.completed", 7, False, "failed"),
    ("turn.completed", 0, True, "failed"),
])
def test_success_needs_a_completed_turn_and_a_clean_exit(
    boundary: str | None, exit_code: int | None, capture_failed: bool, status: str,
) -> None:
    assert turn_status(boundary, exit_code, capture_failed=capture_failed) == status


def message(text: object, *, kind: str = "agent_message", event: str = "item.completed") -> TraceEvent:
    return TraceEvent.model_validate({"type": event, "item": {"type": kind, "text": text}})


@pytest.mark.parametrize("event", [
    message("  "), message(None), message(" [Redacted: secret] "), message("draft", event="item.started"),
    message("thought", kind="reasoning"), TraceEvent(type="turn.completed"),
])
def test_only_a_visible_finished_agent_message_is_a_reply(event: TraceEvent) -> None:
    assert agent_reply(event) is None


def test_replies_and_boundaries_are_read_from_events() -> None:
    assert agent_reply(message("Done, [redacted:key] removed.")) == "Done, [redacted:key] removed."
    assert turn_boundary(TraceEvent(type="turn.failed")) == "turn.failed"
    assert turn_boundary(message("Done")) is None
