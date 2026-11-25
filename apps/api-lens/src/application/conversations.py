"""Project conversation summaries from accepted capture lineage."""

import re
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.run_continuation import ContinuationInfo, describe_continuation
from src.application.run_history import GetRunHistory
from src.domain.trace_reader import read_timed_trace
from src.domain.turn_summary import TurnSummary
from src.infrastructure.capture_files import CaptureFiles, SavedResult
from src.infrastructure.conversation_store import ConversationStore


@dataclass(frozen=True)
class Turn:
    run_id: str
    prompt: str | None
    assistant_markdown: str | None
    submitted_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    status: str
    summary: TurnSummary | None = None


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: UUID | None
    root_run_id: str
    latest_run_id: str
    title: str
    preview: str | None
    last_activity_at: datetime | None
    status: str
    workspace: str | None


@dataclass(frozen=True)
class ThreadDetail(ThreadSummary):
    turns: list[Turn]
    continuation: ContinuationInfo


def preview(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    return " ".join(text.split())[:limit] or None


def legacy_summary(capture: CaptureFiles, finished: bool) -> tuple[str | None, str | None]:
    reply = None
    last_turn = None
    try:
        with closing(capture.read_trace_lines(finished=finished)) as lines:
            for record in read_timed_trace(lines):
                if record.event.type in {"turn.started", "turn.completed", "turn.failed"}:
                    last_turn = record.event.type
                item = getattr(record.event, "item", None)
                if record.event.type != "item.completed" or not isinstance(item, dict):
                    continue
                text = item.get("text")
                if (item.get("type") == "agent_message" and isinstance(text, str) and text.strip()
                        and not re.fullmatch(r"\s*\[redacted:[^\]]+\]\s*", text, re.IGNORECASE)):
                    reply = text
    except (OSError, ValueError):
        return None, None
    return reply, last_turn


class Conversations:
    def __init__(self, store: ConversationStore) -> None:
        self.store = store

    def _turn(self, capture: CaptureFiles, *, details: bool) -> Turn:
        session = capture.read_session()
        result = capture.read_result()
        reply = result.assistant_markdown if result else None
        last_turn = result.last_turn if result else None
        if details and result and result.finished_at is None:
            reply, last_turn = legacy_summary(capture, True)
        status = self._status(capture, result, last_turn)
        return Turn(capture.directory.name, session.prompt if session else None, reply,
                    session.submitted_at if session else None, result.finished_at if result else None,
                    result.exit_code if result else None, status, result.summary if result and details else None)

    @staticmethod
    def _status(capture: CaptureFiles, result: SavedResult | None, last_turn: str | None) -> str:
        if capture.is_active():
            return "active"
        if result is None:
            return "unconfirmed"
        return GetRunHistory._run_status(last_turn, result.exit_code, result.capture_failed)

    def _project(self, root: CaptureFiles, *, details: bool) -> ThreadDetail | None:
        session = root.read_session()
        result = root.read_result()
        thread_id = result.thread_id if result and result.persistence_confirmed else None
        if thread_id is None and (session is None or session.prompt is None):
            return None
        if result is None and not root.is_active() and not (root.directory / "events.timed.jsonl").is_file():
            return None
        chain = self.store.lineage(root)
        for child in chain[1:]:
            child_result = child.read_result()
            if child_result and (child_result.conflicting_thread_id is not None or
                                 (child_result.persistence_confirmed and child_result.thread_id != thread_id)):
                raise ValueError("Conflicting conversation identity.")
        turns = [self._turn(capture, details=details) for capture in chain]
        dates = [date for turn in turns for date in (turn.submitted_at, turn.finished_at) if date is not None]
        latest = turns[-1]
        continuation = describe_continuation(latest.run_id, chain[-1], chain[-1].read_result())
        return ThreadDetail(thread_id, turns[0].run_id, latest.run_id,
                            preview(turns[0].prompt, 80) or "Conversa anterior",
                            preview(latest.prompt or latest.assistant_markdown, 140), max(dates, default=None),
                            latest.status, session.workspace if session else None, turns, continuation)

    def list(self) -> list[ThreadSummary]:
        threads = [thread for root in self.store.roots() if (thread := self._project(root, details=False))]
        ids = [thread.thread_id for thread in threads if thread.thread_id is not None]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate conversation identity.")
        threads.sort(key=lambda thread: thread.root_run_id)
        threads.sort(key=lambda thread: thread.last_activity_at.timestamp() if thread.last_activity_at else float("-inf"),
                     reverse=True)
        return [ThreadSummary(t.thread_id, t.root_run_id, t.latest_run_id, t.title, t.preview,
                              t.last_activity_at, t.status, t.workspace) for t in threads]

    def by_run(self, run_id: str) -> ThreadDetail | None:
        capture = self.store.find(run_id)
        root = self.store.root_for(capture)
        detail = self._project(root, details=True)
        if detail and not any(turn.run_id == run_id for turn in detail.turns):
            raise ValueError("Invalid conversation lineage.")
        if detail and detail.thread_id is not None:
            self.list()
        return detail

    def by_thread(self, thread_id: UUID) -> ThreadDetail | None:
        matches = [thread for thread in self.list() if thread.thread_id == thread_id]
        return self.by_run(matches[0].root_run_id) if matches else None
