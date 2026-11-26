import json
from pathlib import Path
from typing import Any

import pytest

from src.backend.domain.turn.turn_outcome import agent_reply
from src.backend.domain.turn.turn_summary import TurnEvidence
from src.executor.agents.base import AgentError
from src.executor.agents.claude.translation import ClaudeTranslation
from src.shared.trace_events import TimedEvent, TraceEvent

RECORDINGS = Path(__file__).parent / "recordings"
SESSION = "00000000-0000-4000-8000-000000000001"


def recorded(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (RECORDINGS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]


def translated(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    translation = ClaudeTranslation()
    events = [event for message in messages for event in translation.observe(message)]
    ending = translation.ending()
    closing = translation.incomplete()
    return [*events, *closing] if ending is None else [*events, *closing, ending]


def completed_items(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e["item"] for e in events if e["type"] == "item.completed" and e["item"]["type"] == kind]


def result(**fields: Any) -> dict[str, Any]:
    return {"type": "result", "is_error": False, "terminal_reason": "completed", **fields}


def tool_exchange(name: str, arguments: dict[str, Any], **outcome: Any) -> list[dict[str, Any]]:
    use = {"type": "tool_use", "id": "t1", "name": name, "input": arguments}
    answer = {"type": "tool_result", "tool_use_id": "t1", **outcome}
    return translated([
        {"type": "assistant", "message": {"id": "m1", "content": [use]}},
        {"type": "user", "message": {"content": [answer]}},
    ])


def test_a_turn_starts_with_the_session_and_ends_completed() -> None:
    events = translated(recorded("failing_command_and_edit"))
    assert events[:2] == [{"type": "thread.started", "thread_id": SESSION}, {"type": "turn.started"}]
    assert events[-1]["type"] == "turn.completed"


def test_failed_commands_keep_the_exit_code_written_in_their_output() -> None:
    commands = completed_items(translated(recorded("failing_command_and_edit")), "command_execution")
    assert [(c["status"], c["exit_code"]) for c in commands] == [("failed", 1), ("failed", 7), ("completed", None)]
    assert commands[0]["command"] == "python3 -m unittest"
    assert commands[0]["aggregated_output"].startswith("Exit code 1\n")


def test_every_started_item_is_completed_under_the_same_id() -> None:
    events = translated(recorded("failing_command_and_edit"))
    started = {e["item"]["id"] for e in events if e["type"] == "item.started"}
    completed = {e["item"]["id"] for e in events if e["type"] == "item.completed"}
    assert started and started <= completed


def test_an_edit_is_a_file_change_and_a_read_is_a_tool_call() -> None:
    events = translated(recorded("failing_command_and_edit"))
    [change] = completed_items(events, "file_change")
    [read] = completed_items(events, "tool_call")
    assert change["status"] == "completed"
    assert change["changes"] == [{"path": "/workspace/calc.py", "kind": "update"}]
    assert (read["tool"], read["status"]) == ("Read", "completed")
    assert "def add" in read["result"]


@pytest.mark.parametrize(("detail", "kind"), [("create", "add"), ("update", "update")])
def test_a_write_reports_whether_it_created_the_file(detail: str, kind: str) -> None:
    messages = [
        {"type": "assistant", "message": {"id": "m1", "content": [
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "/workspace/new.py"}}]}},
        {"type": "user", "tool_use_result": {"type": detail},
         "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "done"}]}},
    ]
    [change] = completed_items(translated(messages), "file_change")
    assert change["changes"] == [{"path": "/workspace/new.py", "kind": kind}]


def test_a_failed_tool_carries_its_error() -> None:
    [call] = completed_items(
        tool_exchange("Read", {"file_path": "/x"}, is_error=True, content="No such file"), "tool_call")
    assert (call["status"], call["error"]) == ("failed", {"message": "No such file"})


def test_a_failure_without_an_exit_code_does_not_invent_one() -> None:
    [command] = completed_items(
        tool_exchange("Bash", {"command": "x"}, is_error=True, content="Command timed out"), "command_execution")
    assert (command["status"], command["exit_code"]) == ("failed", None)


def test_a_denied_command_is_declined_and_the_last_message_is_the_reply() -> None:
    events = translated(recorded("background_subagent"))
    commands = completed_items(events, "command_execution")
    replies = [agent_reply(TraceEvent.model_validate(e)) for e in events]
    assert [(c["status"], c["exit_code"]) for c in commands] == [("declined", None)]
    assert [reply for reply in replies if reply][-1].startswith("Yes, calc.py looks correct")


def test_two_results_make_one_ending_with_the_usage_of_both() -> None:
    events = translated(recorded("background_subagent"))
    assert [e["type"] for e in events if e["type"].startswith(("thread.", "turn."))] == [
        "thread.started", "turn.started", "turn.completed"]
    assert events[-1]["usage"] == {
        "input_tokens": 4 + 5306 + 17869 + 2 + 879 + 12170,
        "cached_input_tokens": 17869 + 12170,
        "cache_write_input_tokens": 5306 + 879,
        "output_tokens": 674 + 87,
        "reasoning_output_tokens": 210,
    }


def test_the_subagent_s_own_activity_is_kept_out_of_the_main_flow() -> None:
    calls = completed_items(translated(recorded("background_subagent")), "tool_call")
    assert [call["tool"] for call in calls] == ["Agent"]


def test_the_backend_summarises_a_translated_turn() -> None:
    evidence = TurnEvidence()
    for offset, event in enumerate(translated(recorded("failing_command_and_edit"))):
        evidence.observe(TimedEvent(elapsed_ns=offset, event=TraceEvent.model_validate(event)))
    summary = evidence.finish(interrupted=False)
    assert (summary.actions, summary.failures) == (5, 2)
    assert [file.path for file in summary.files] == ["/workspace/calc.py"]
    assert [(test.status, test.exit_code) for test in summary.tests] == [("failed", 1), ("completed", None)]


@pytest.mark.parametrize("fields", [
    {"is_error": True}, {"terminal_reason": "max_turns"}, {"is_error": None}, {"terminal_reason": None},
])
def test_only_a_clean_result_completes_the_turn(fields: dict[str, Any]) -> None:
    [ending] = translated([result(**fields, result="Not logged in", subtype="success")])
    assert ending == {"type": "turn.failed", "error": {"message": "Not logged in"}}


def test_the_last_result_decides_the_ending() -> None:
    [ending] = translated([result(), result(is_error=True)])
    assert ending == {"type": "turn.failed", "error": {"message": "completed"}}


def test_without_a_result_there_is_no_ending() -> None:
    assert translated([{"type": "something_new", "is_error": False}]) == []


def test_unknown_messages_and_results_of_unknown_tools_are_ignored() -> None:
    assert translated([
        {"type": "system", "subtype": "permission_denied", "message": "text"},
        {"type": "user", "message": {"content": "plain text"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "nope"}]}},
        {"type": "assistant", "message": {"id": "m", "content": [{"type": "new_block"}]}},
    ]) == []


def test_reasoning_with_text_is_kept() -> None:
    block = {"type": "thinking", "thinking": "Check the test first."}
    [item] = completed_items(translated([{"type": "assistant", "message": {"id": "m", "content": [block]}}]), "reasoning")
    assert item["text"] == "Check the test first."


def test_a_resumed_turn_refuses_another_session() -> None:
    translation = ClaudeTranslation(expected_session="00000000-0000-4000-8000-000000000009")
    with pytest.raises(AgentError):
        translation.observe(recorded("resumed_session")[0])
