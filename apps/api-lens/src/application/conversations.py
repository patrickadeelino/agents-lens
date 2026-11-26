"""Project conversation summaries from accepted capture lineage."""

import re
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from src.application.conversation_ports import (
    ConversationRepository,
    ConversationResult,
    ConversationRun,
)
from src.application.run_continuation import ContinuationInfo, describe_continuation
from src.application.run_history import GetRunHistory
from src.domain.trace_reader import read_timed_trace
from src.domain.turn_summary import TurnSummary
from src.observability import event_log


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


def legacy_summary(capture: ConversationRun, finished: bool) -> tuple[str | None, str | None]:
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


@dataclass(frozen=True)
class ThreadList:
    threads: list[ThreadSummary]
    unavailable: int


@dataclass(frozen=True)
class _Scan:
    threads: list[ThreadDetail]
    unavailable: int
    duplicated: set[UUID]


class Conversations[Run: ConversationRun]:
    def __init__(self, store: ConversationRepository[Run]) -> None:
        self.store = store

    def _turn(self, capture: Run, *, details: bool) -> Turn:
        session = capture.read_session()
        result = capture.read_result()
        reply = result.assistant_markdown if result else None
        last_turn = result.last_turn if result else None
        if details and result and result.finished_at is None:
            reply, last_turn = legacy_summary(capture, True)
        status = self._status(capture, result, last_turn)
        return Turn(capture.run_id, session.prompt if session else None, reply,
                    session.submitted_at if session else None, result.finished_at if result else None,
                    result.exit_code if result else None, status, result.summary if result and details else None)

    @staticmethod
    def _status(capture: ConversationRun, result: ConversationResult | None, last_turn: str | None) -> str:
        if capture.is_active():
            return "active"
        if result is None:
            return "unconfirmed"
        return GetRunHistory._run_status(last_turn, result.exit_code, result.capture_failed)

    def _project(self, root: Run, *, details: bool) -> ThreadDetail | None:
        session = root.read_session()
        result = root.read_result()
        thread_id = result.thread_id if result and result.persistence_confirmed else None
        if thread_id is None and (session is None or session.prompt is None):
            return None
        if result is None and not root.is_active() and not root.has_trace():
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
                            preview(turns[0].prompt, 80) or "Earlier conversation",
                            preview(latest.prompt or latest.assistant_markdown, 140), max(dates, default=None),
                            latest.status, session.workspace if session else None, turns, continuation)

    def _scan(self) -> _Scan:
        """Project every conversation, leaving out the damaged ones without failing the others."""
        roots, unavailable = self.store.roots()
        threads = []
        for root in roots:
            try:
                thread = self._project(root, details=False)
            except (OSError, ValueError):
                event_log("conversation.skipped", "history", "failed", error_code="capture_unreadable")
                unavailable += 1
                continue
            if thread:
                threads.append(thread)
        counts = Counter(thread.thread_id for thread in threads if thread.thread_id is not None)
        duplicated = {thread_id for thread_id, total in counts.items() if total > 1}
        readable = [thread for thread in threads if thread.thread_id not in duplicated]
        return _Scan(readable, unavailable + len(threads) - len(readable), duplicated)

    def list(self) -> ThreadList:
        scan = self._scan()
        threads = scan.threads
        threads.sort(key=lambda thread: thread.root_run_id)
        threads.sort(key=lambda thread: thread.last_activity_at.timestamp() if thread.last_activity_at else float("-inf"),
                     reverse=True)
        return ThreadList([ThreadSummary(t.thread_id, t.root_run_id, t.latest_run_id, t.title, t.preview,
                                         t.last_activity_at, t.status, t.workspace) for t in threads], scan.unavailable)

    def by_run(self, run_id: str) -> ThreadDetail | None:
        capture = self.store.find(run_id)
        root = self.store.root_for(capture)
        detail = self._project(root, details=True)
        if detail and not any(turn.run_id == run_id for turn in detail.turns):
            raise ValueError("Invalid conversation lineage.")
        if detail and detail.thread_id is not None and detail.thread_id in self._scan().duplicated:
            raise ValueError("Duplicate conversation identity.")
        return detail

    def by_thread(self, thread_id: UUID) -> ThreadDetail | None:
        scan = self._scan()
        for thread in scan.threads:
            if thread.thread_id == thread_id:
                return self.by_run(thread.root_run_id)
        # With damaged captures, a missing thread cannot be told apart from an unreadable one.
        if scan.unavailable:
            raise ValueError("Conversation may be unreadable.")
        return None
