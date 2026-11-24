"""Persist application captures on the local filesystem."""

import re
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from os import link
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from threading import Lock
from typing import BinaryIO, ClassVar, Self, TextIO
from uuid import UUID

from anyio.to_thread import run_sync
from pydantic import BaseModel, ConfigDict

from src.application.run_continuation import RunSession
from src.domain.turn_summary import TurnSummary

TRACE_NAME = "events.timed.jsonl"
RESULT_NAME = "result.json"
SESSION_NAME = "session.json"
SUCCESSOR_NAME = "next-run"


class SavedResult(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    exit_code: int | None
    thread_id: UUID | None = None
    persistence_confirmed: bool = False
    assistant_markdown: str | None = None
    last_turn: str | None = None
    finished_at: datetime | None = None
    conflicting_thread_id: UUID | None = None
    capture_failed: bool = False
    summary: TurnSummary | None = None


@dataclass(frozen=True)
class CaptureFiles:
    directory: Path
    _active: ClassVar[set[Path]] = set()
    _activity_lock: ClassVar[Lock] = Lock()

    @classmethod
    def create(cls, root: Path, *, prefix: str) -> Self:
        root.mkdir(mode=0o700, exist_ok=True)
        return cls(Path(mkdtemp(prefix=prefix, dir=root)))

    @classmethod
    def find(cls, root: Path, run_id: str) -> Self:
        directory = root / run_id
        if directory.parent != root or directory.is_symlink() or not directory.is_dir():
            raise FileNotFoundError("Capture directory does not exist.")
        return cls(directory)

    def open_trace(self) -> TextIO:
        return (self.directory / TRACE_NAME).open("x", encoding="utf-8")

    @contextmanager
    def track_activity(self) -> Iterator[None]:
        with self._activity_lock:
            self._active.add(self.directory)
        try:
            yield
        finally:
            with self._activity_lock:
                self._active.discard(self.directory)

    def is_active(self) -> bool:
        with self._activity_lock:
            return self.directory in self._active

    def ensure_trace(self) -> None:
        (self.directory / TRACE_NAME).touch(exist_ok=True)

    def write_result(
        self, exit_code: int | None, *, thread_id: UUID | None = None, persistence_confirmed: bool = False,
        assistant_markdown: str | None = None, last_turn: str | None = None, finished_at: datetime | None = None,
        conflicting_thread_id: UUID | None = None,
        capture_failed: bool = False, summary: TurnSummary | None = None,
    ) -> None:
        result = SavedResult(exit_code=exit_code, thread_id=thread_id, persistence_confirmed=persistence_confirmed,
                             assistant_markdown=assistant_markdown, last_turn=last_turn, finished_at=finished_at,
                             conflicting_thread_id=conflicting_thread_id, capture_failed=capture_failed, summary=summary)
        fields = set()
        if thread_id is None:
            fields.add("thread_id")
        if not persistence_confirmed:
            fields.add("persistence_confirmed")
        if not capture_failed:
            fields.add("capture_failed")
        fields.update(name for name in ("assistant_markdown", "last_turn", "finished_at", "conflicting_thread_id", "summary")
                      if getattr(result, name) is None)
        self._publish_once(RESULT_NAME, result.model_dump_json(exclude=fields) + "\n")

    def write_session(self, session: RunSession) -> None:
        self._publish_once(SESSION_NAME, session.model_dump_json())

    def read_session(self) -> RunSession | None:
        try:
            contents = self._saved_file(SESSION_NAME).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return RunSession.model_validate_json(contents)

    def reserve_successor(self, successor: Self) -> None:
        self._publish_once(SUCCESSOR_NAME, successor.directory.name)

    def successor(self) -> Self | None:
        try:
            name = self._saved_file(SUCCESSOR_NAME).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        if not re.fullmatch(r"codex-prompt-[A-Za-z0-9_-]+", name):
            raise ValueError("Invalid continuation link.")
        try:
            return self.find(self.directory.parent, name)
        except FileNotFoundError as error:
            raise ValueError("Invalid continuation link.") from error

    def discard_unpublished(self) -> None:
        (self.directory / SESSION_NAME).unlink(missing_ok=True)
        self.directory.rmdir()

    def latest_run_id(self) -> str:
        current = self
        visited: set[Path] = set()
        while current.directory not in visited:
            visited.add(current.directory)
            child = current.successor()
            if child is None:
                return current.directory.name
            session = child.read_session()
            if session is None or session.previous_run_id != current.directory.name:
                raise ValueError("Invalid continuation link.")
            current = child
        raise ValueError("Continuation links contain a cycle.")

    def _publish_once(self, name: str, contents: str) -> None:
        with NamedTemporaryFile(dir=self.directory, mode="w", encoding="utf-8", delete_on_close=False) as temporary:
            temporary.write(contents)
            temporary.close()
            link(temporary.name, self.directory / name)

    def read_trace_lines(self, *, finished: bool) -> Generator[str]:
        with self.open_saved_trace() as trace:
            for line in trace:
                # An append in progress may end mid-line or mid-character.
                if not finished and not line.endswith(b"\n"):
                    break
                yield line.decode("utf-8")

    def open_saved_trace(self) -> BinaryIO:
        return self._saved_file(TRACE_NAME).open("rb")

    @contextmanager
    def open_tail(self) -> Iterator["CaptureTail"]:
        with self.open_saved_trace() as trace:
            yield CaptureTail(self, trace)

    def read_result(self) -> SavedResult | None:
        try:
            result = self._saved_file(RESULT_NAME).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return SavedResult.model_validate_json(result) if result else None

    def _saved_file(self, name: str) -> Path:
        path = self.directory / name
        if path.is_symlink():
            raise PermissionError("Capture files must be local to their run directory.")
        if not path.is_file():
            raise FileNotFoundError("Capture file does not exist.")
        return path


class CaptureTail:
    """Read one append-only trace, keeping the position when it reaches the writer."""

    def __init__(self, capture: CaptureFiles, trace: BinaryIO) -> None:
        self._capture = capture
        self._trace = trace

    async def read(self) -> str | SavedResult | None:
        return await run_sync(self._read)

    def _read(self) -> str | SavedResult | None:
        line = self._line()
        if line:
            return line
        if self._capture.is_active():
            return None

        # The writer may have flushed its final record and exited after the first read.
        result = self._capture.read_result()
        return self._line(finished=result is not None) or result or SavedResult(exit_code=None)

    def _line(self, *, finished: bool = False) -> str:
        start = self._trace.tell()
        line = self._trace.readline()
        if line and not finished and not line.endswith(b"\n"):
            self._trace.seek(start)
            return ""
        return line.decode("utf-8")
