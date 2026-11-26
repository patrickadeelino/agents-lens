"""Translate the messages of one Claude Code run into capture events."""

from collections.abc import Iterator
from uuid import UUID

from src.executor.agents.base import AgentError, Json
from src.executor.agents.claude.items import completed_item, started_item
from src.executor.agents.claude.subagents import (
    Subagents,
    answered_status,
    notified_status,
    observation,
)

_TEXT_BLOCKS = {"text": ("agent_message", "text"), "thinking": ("reasoning", "thinking")}


class ClaudeTranslation:
    """What one run has shown so far: its session, its open tool calls and its latest result."""

    def __init__(self, expected_session: str | None = None) -> None:
        self._expected_session = expected_session
        self._started = False
        self._open: dict[str, Json] = {}
        self._denied: set[str] = set()
        self._texts = 0
        self._result: Json | None = None
        self._usage: dict[str, int] = {}
        self._subagents = Subagents()

    def observe(self, message: Json) -> list[Json]:
        """Return the events this message adds. Unknown messages add none."""
        kind = message.get("type")
        if kind == "system":
            return self._system(message)
        if kind == "result":
            self._keep_result(message)
            return []
        if kind not in {"assistant", "user"}:
            return []
        return self._message(message)

    def ending(self) -> Json | None:
        """The turn's outcome, decided by the latest result: a background subagent adds a second one."""
        result = self._result
        if result is None:
            return None
        if result.get("is_error") is False and result.get("terminal_reason") == "completed":
            return {"type": "turn.completed", "usage": self._usage} if self._usage else {"type": "turn.completed"}
        reason = result.get("result") or result.get("terminal_reason") or "failed"
        return {"type": "turn.failed", "error": {"message": str(reason)}}

    def incomplete(self) -> list[Json]:
        """Mark the subagents whose end was never observed."""
        return self._subagents.incomplete()

    def _system(self, message: Json) -> list[Json]:
        subtype, call = message.get("subtype"), str(message.get("tool_use_id"))
        if subtype == "permission_denied":
            self._denied.add(call)
        if subtype == "task_started":
            return self._subagents.start(call)
        if subtype == "task_notification":
            return self._subagents.end(call, notified_status(message.get("status")))
        if subtype != "init" or self._started:
            return []
        session = str(UUID(message["session_id"]))
        if self._expected_session not in {None, session}:
            raise AgentError
        self._started = True
        self._subagents.root = session
        return [{"type": "thread.started", "thread_id": session}, {"type": "turn.started"}]

    def _message(self, message: Json) -> list[Json]:
        parent = message.get("parent_tool_use_id")
        if parent and not self._subagents.known(parent):
            return []
        content = message["message"].get("content")
        if not isinstance(content, list):
            return []
        return [event for block in content for event in self._block_events(block, message, parent)]

    def _block_events(self, block: Json, message: Json, parent: str | None) -> Iterator[Json]:
        event = self._block(block, message, parent)
        if event is not None and parent:
            yield from self._subagents.start(parent)
            yield observation(parent, event)
        elif event is not None:
            yield event
        if block.get("type") != "tool_result":
            return
        status = answered_status(block, message.get("tool_use_result"))
        if status:
            yield from self._subagents.end(str(block.get("tool_use_id")), status)

    def _block(self, block: Json, message: Json, parent: str | None) -> Json | None:
        kind = block.get("type")
        if kind == "tool_use":
            self._subagents.request(block, parent)
            item = self._open[block["id"]] = started_item(block)
            return {"type": "item.started", "item": item}
        if kind == "tool_result":
            return self._tool_result(block, message)
        # Text in a user message is what was sent to the agent, not something it said.
        if kind in _TEXT_BLOCKS and message.get("type") == "assistant":
            return self._text(block, message["message"].get("id"))
        return None

    def _tool_result(self, block: Json, message: Json) -> Json | None:
        call = block.get("tool_use_id")
        started = self._open.pop(str(call), None)
        if started is None:
            return None
        item = completed_item(started, block, message.get("tool_use_result"), denied=call in self._denied)
        return {"type": "item.completed", "item": item}

    def _text(self, block: Json, message_id: str | None) -> Json | None:
        kind, field = _TEXT_BLOCKS[block["type"]]
        text = block.get(field)
        if not text:
            return None
        self._texts += 1
        return {"type": "item.completed", "item": {"id": f"{message_id}:{self._texts}", "type": kind, "text": text}}

    def _keep_result(self, result: Json) -> None:
        """Add up the usage of every result. It covers the main agent, not its subagents."""
        self._result = result
        usage = result.get("usage")
        if not isinstance(usage, dict):
            return
        for name, value in token_counts(usage).items():
            self._usage[name] = self._usage.get(name, 0) + value


def token_counts(usage: Json) -> dict[str, int]:
    """Count input as Codex does, with the cached part included, so both agents can be compared."""
    cached = usage.get("cache_read_input_tokens", 0)
    written = usage.get("cache_creation_input_tokens", 0)
    return {
        "input_tokens": usage.get("input_tokens", 0) + cached + written,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": written,
        "output_tokens": usage.get("output_tokens", 0),
        "reasoning_output_tokens": (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0),
    }
