from typing import Any

import pytest

from src.executor.agents.base import Json
from src.executor.agents.claude.translation import ClaudeTranslation

from .test_translation import recorded, translated

ROOT = "00000000-0000-4000-8000-000000000002"
CHILD = "toolu_LpLimw"
INIT = {"type": "system", "subtype": "init", "session_id": ROOT}


def delegations(events: list[Json]) -> list[Json]:
    return [event for event in events if event["type"].startswith("delegation.")]


def observed(events: list[Json]) -> list[tuple[str, str]]:
    return [
        (event["thread_id"], event["observation"]["type"])
        for event in events if event["type"] == "delegation.event"
    ]


def use(call: str, name: str = "Agent", parent: str | None = None, **arguments: Any) -> Json:
    block = {"type": "tool_use", "id": call, "name": name, "input": arguments}
    return {"type": "assistant", "parent_tool_use_id": parent, "message": {"id": f"m-{call}", "content": [block]}}


def answer(call: str, detail: Json | None = None, **outcome: Any) -> Json:
    block = {"type": "tool_result", "tool_use_id": call, **outcome}
    return {"type": "user", "tool_use_result": detail, "message": {"content": [block]}}


def task(subtype: str, call: str, **fields: Any) -> Json:
    return {"type": "system", "subtype": subtype, "tool_use_id": call, **fields}


def test_a_started_subagent_is_linked_to_the_main_session() -> None:
    [edge, *_] = delegations(translated(recorded("background_subagent")))
    assert edge == {
        "type": "delegation.edge", "parent_thread_id": ROOT, "parent_turn_id": ROOT,
        "item_id": CHILD, "child_thread_id": CHILD, "child_index": 1,
    }


def test_the_recorded_subagent_shows_its_work_and_how_it_ended() -> None:
    events = delegations(translated(recorded("background_subagent")))
    assert [kind for _, kind in observed(events)] == [
        "turn.started", "item.started", "item.completed", "item.completed", "turn.completed", "subagent.completed",
    ]
    assert {thread for thread, _ in observed(events)} == {CHILD}
    assert events[-1]["observation"]["item"] == {"agentPath": "general-purpose"}
    assert "delegation.incomplete" not in [event["type"] for event in events]


def test_launching_in_the_background_does_not_finish_the_subagent() -> None:
    events = translated([
        INIT, use("a"), task("task_started", "a"),
        answer("a", {"isAsync": True, "status": "async_launched"}, content="launched"),
    ])
    assert observed(events) == [("a", "turn.started")]
    assert events[-1] == {"type": "delegation.incomplete", "thread_id": "a", "parent_thread_id": ROOT}


@pytest.mark.parametrize(("reported", "shown"), [
    ("failed", "failed"), ("stopped", "interrupted"), ("something_new", "interrupted"), (None, "interrupted"),
])
def test_only_a_reported_completion_is_shown_as_success(reported: str | None, shown: str) -> None:
    events = delegations(translated([
        INIT, use("a"), task("task_started", "a"), task("task_notification", "a", status=reported),
    ]))
    assert events[-1]["observation"] == {"type": "turn.completed", "status": shown}
    assert len(events) == 3


@pytest.mark.parametrize(("outcome", "detail", "status"), [
    ({"content": "done"}, {"status": "completed"}, "completed"),
    ({"is_error": True, "content": "boom"}, None, "failed"),
])
def test_a_subagent_that_answers_in_place_ends_with_its_result(
    outcome: Json, detail: Json | None, status: str,
) -> None:
    child_text = {"type": "assistant", "parent_tool_use_id": "a",
                  "message": {"id": "m", "content": [{"type": "text", "text": "Checking"}]}}
    events = delegations(translated([INIT, use("a"), child_text, answer("a", detail, **outcome)]))
    ended = [e["observation"] for e in events if e.get("observation", {}).get("type") == "turn.completed"]
    assert ended == [{"type": "turn.completed", "status": status}]


def test_a_subagent_can_start_another_and_each_gets_its_own_number() -> None:
    events = delegations(translated([
        INIT, use("a"), task("task_started", "a"), use("b", parent="a"), use("c", "Read", parent="b"),
    ]))
    edges = [(e["parent_thread_id"], e["child_thread_id"], e["child_index"])
             for e in events if e["type"] == "delegation.edge"]
    assert edges == [(ROOT, "a", 1), ("a", "b", 2)]


def test_a_refused_or_unrelated_task_creates_no_subagent() -> None:
    events = translated([
        INIT, use("a"), answer("a", is_error=True, content="denied"),
        use("shell", "Bash"), task("task_started", "shell"), task("task_notification", "shell", status="completed"),
        use("x", "Read", parent="unknown"),
    ])
    assert delegations(events) == []


def test_text_sent_to_a_subagent_is_not_shown_as_its_reply() -> None:
    prompt = {"type": "user", "parent_tool_use_id": "a",
              "message": {"content": [{"type": "text", "text": "Review the file"}]}}
    translation = ClaudeTranslation()
    translation.observe(INIT)
    translation.observe(use("a"))
    assert translation.observe(prompt) == []
