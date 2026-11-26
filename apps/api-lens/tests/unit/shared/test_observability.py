from re import fullmatch
from typing import Any, cast

import pytest
from opentelemetry.trace import Span

from src.shared import observability
from src.shared.trace_events import TraceEvent


class RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, name: str, value: object) -> None:
        self.attributes[name] = value

    def set_status(self, status: object) -> None:
        return None


@pytest.mark.parametrize("item_id", ["item_12", "toolu_01EhFurjYtorBsr2HDLpLimw"])
def test_command_ids_of_both_agents_are_accepted(item_id: str) -> None:
    assert fullmatch(observability._COMMAND_ID, item_id)


def test_free_text_is_not_accepted_as_a_command_id() -> None:
    assert fullmatch(observability._COMMAND_ID, "toolu_ a secret") is None


def test_a_declined_command_is_reported_as_failed() -> None:
    span = RecordingSpan()
    observability.finish_command_span(cast(Span, span), "declined", None)
    assert span.attributes == {"agent_lens.outcome": "failed", "agent_lens.error_code": "command_failed"}


def test_generic_tool_calls_keep_their_type() -> None:
    event = TraceEvent.model_validate({"type": "item.completed", "item": {"type": "tool_call"}})
    assert observability.SseRecordDetails.from_event(event, 1).item_type == "tool_call"
