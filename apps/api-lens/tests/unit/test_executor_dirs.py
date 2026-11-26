from pathlib import Path

import pytest

from src.executor.main import prepare_executor_dirs


def test_clean_checkout_creates_only_private_executor_directories(tmp_path: Path) -> None:
    state, socket = prepare_executor_dirs(tmp_path)
    assert prepare_executor_dirs(tmp_path) == (state, socket)
    assert state.is_dir() and state.parent == socket.parent.parent == tmp_path.resolve()
    assert socket.parent.stat().st_mode & 0o777 == 0o711
    assert {path.name for path in state.parent.iterdir()} == {"state", "ipc"}


def test_rejects_symlinked_state(tmp_path: Path) -> None:
    (tmp_path / "state").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        prepare_executor_dirs(tmp_path)
