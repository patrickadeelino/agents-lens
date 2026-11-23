"""Persist application captures on the local filesystem."""

import json
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from threading import Lock
from typing import BinaryIO, ClassVar, Self, TextIO

from anyio.to_thread import run_sync
from pydantic import BaseModel, ConfigDict

TRACE_NAME = "events.timed.jsonl"
RESULT_NAME = "result.json"


class SavedResult(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    exit_code: int | None


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

    def write_result(self, exit_code: int | None) -> None:
        with (self.directory / RESULT_NAME).open("x", encoding="utf-8") as result:
            print(json.dumps({"exit_code": exit_code}), file=result)

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
