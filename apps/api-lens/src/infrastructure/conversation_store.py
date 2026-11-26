"""Traverse capture lineage without following filesystem links."""

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

from src.infrastructure.capture_files import CaptureFiles


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def find(self, run_id: str) -> CaptureFiles:
        return CaptureFiles.find(self.root, run_id)

    def roots(self) -> tuple[list[CaptureFiles], int]:
        """Return the readable first runs and how many unreadable runs no conversation links to."""
        if not self.root.exists():
            return [], 0
        roots: list[CaptureFiles] = []
        unreadable: set[str] = set()
        linked: set[str] = set()
        for capture in self._prompt_captures():
            # A damaged link is reported by the lineage of the conversation that owns it.
            with suppress(OSError, ValueError):
                if name := capture.successor_name():
                    linked.add(name)
            try:
                session = capture.read_session()
            except (OSError, ValueError):
                unreadable.add(capture.directory.name)
                continue
            if session is None or session.previous_run_id is None:
                roots.append(capture)
        # A linked run is counted once, through the conversation that fails to read it.
        return roots, len(unreadable - linked)

    def _prompt_captures(self) -> Iterator[CaptureFiles]:
        for path in self.root.iterdir():
            if not path.is_symlink() and path.is_dir() and path.name.startswith("codex-prompt-"):
                yield CaptureFiles(path)

    def lineage(self, root: CaptureFiles) -> list[CaptureFiles]:
        chain = [root]
        visited = {root.directory.name}
        while (child := chain[-1].successor()) is not None:
            child_id = child.directory.name
            session = child.read_session()
            if child_id in visited or session is None or session.previous_run_id != chain[-1].directory.name:
                raise ValueError("Invalid conversation lineage.")
            visited.add(child_id)
            chain.append(child)
        return chain

    def root_for(self, capture: CaptureFiles) -> CaptureFiles:
        visited: set[str] = set()
        while True:
            run_id = capture.directory.name
            if run_id in visited:
                raise ValueError("Invalid conversation lineage.")
            visited.add(run_id)
            session = capture.read_session()
            if session is None or session.previous_run_id is None:
                return capture
            try:
                parent = self.find(session.previous_run_id)
            except FileNotFoundError as error:
                raise ValueError("Invalid conversation lineage.") from error
            child = parent.successor()
            if child is None or child.directory != capture.directory:
                raise ValueError("Invalid conversation lineage.")
            capture = parent
