"""Translate the messages of one Claude Code run into capture events."""

from uuid import UUID

from src.executor.agents.base import AgentError, Json
from src.executor.agents.claude.items import completed_item, started_item

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

    def observe(self, message: Json) -> list[Json]:
        """Return the events this message adds. Unknown messages add none."""
        kind = message.get("type")
        if kind == "system":
            return self._system(message)
        if kind == "result":
            self._keep_result(message)
            return []
        # Messages with a parent belong to a subagent, which the main flow does not show yet.
        if kind not in {"assistant", "user"} or message.get("parent_tool_use_id"):
            return []
        return self._blocks(message)

    def ending(self) -> Json | None:
        """The turn's outcome, decided by the latest result: a background subagent adds a second one."""
        result = self._result
        if result is None:
            return None
        if result.get("is_error") is False and result.get("terminal_reason") == "completed":
            return {"type": "turn.completed", "usage": self._usage} if self._usage else {"type": "turn.completed"}
        reason = result.get("result") or result.get("terminal_reason") or "failed"
        return {"type": "turn.failed", "error": {"message": str(reason)}}

    def _system(self, message: Json) -> list[Json]:
        if message.get("subtype") == "permission_denied":
            self._denied.add(str(message.get("tool_use_id")))
        if message.get("subtype") != "init" or self._started:
            return []
        session = str(UUID(message["session_id"]))
        if self._expected_session not in {None, session}:
            raise AgentError
        self._started = True
        return [{"type": "thread.started", "thread_id": session}, {"type": "turn.started"}]

    def _blocks(self, message: Json) -> list[Json]:
        content = message["message"].get("content")
        if not isinstance(content, list):
            return []
        events = (self._block(block, message) for block in content)
        return [event for event in events if event is not None]

    def _block(self, block: Json, message: Json) -> Json | None:
        kind = block.get("type")
        if kind == "tool_use":
            item = self._open[block["id"]] = started_item(block)
            return {"type": "item.started", "item": item}
        if kind == "tool_result":
            return self._tool_result(block, message)
        if kind in _TEXT_BLOCKS:
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
