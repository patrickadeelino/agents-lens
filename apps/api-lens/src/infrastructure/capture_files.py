"""Persist application captures on the local filesystem."""

import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Self, TextIO


@dataclass(frozen=True)
class CaptureFiles:
    directory: Path

    @classmethod
    def create(cls, root: Path, *, prefix: str) -> Self:
        root.mkdir(mode=0o700, exist_ok=True)
        return cls(Path(mkdtemp(prefix=prefix, dir=root)))

    def open_trace(self) -> TextIO:
        return (self.directory / "events.timed.jsonl").open("x", encoding="utf-8")

    def write_result(self, exit_code: int | None) -> None:
        with (self.directory / "result.json").open("x", encoding="utf-8") as result:
            print(json.dumps({"exit_code": exit_code}), file=result)
