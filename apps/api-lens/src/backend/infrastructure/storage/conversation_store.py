"""Find saved captures without following filesystem links."""

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

from src.backend.application.conversation.run_continuation import RunSession
from src.backend.infrastructure.storage.capture_files import CaptureFiles


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def find(self, run_id: str) -> CaptureFiles:
        return CaptureFiles.find(self.root, run_id)

    def create(self, session: RunSession) -> CaptureFiles:
        capture = CaptureFiles.create(self.root, prefix="codex-prompt-")
        capture.write_session(session)
        return capture

    def any_active(self) -> bool:
        return CaptureFiles.any_active(self.root)

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
