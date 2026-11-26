from pathlib import Path

import pytest

from src.executor.agents.claude.run import ClaudeRun
from src.executor.agents.configured import ExecutorSetup
from src.executor.startup import executor_parser, executor_setup


def started(root: Path, *extra: str, codex_key: str = "codex-key\n") -> ExecutorSetup:
    for name in ("codex", "claude"):
        (root / name).touch()
    args = executor_parser().parse_args([
        "--codex-bin", str(root / "codex"), "--model", "codex-model", "--data-dir", str(root),
        "--workspace-root", str(root), *extra,
    ])
    return executor_setup(args, [codex_key], root / "state")


def claude_options(root: Path, key: str) -> tuple[str, ...]:
    (root / "key").write_text(key, encoding="utf-8")
    return (
        "--claude-bin", str(root / "claude"), "--claude-model", "claude-model",
        "--claude-key-file", str(root / "key"),
    )


@pytest.mark.parametrize("key", ["", "\n"])
def test_without_a_claude_key_only_codex_is_offered(tmp_path: Path, key: str) -> None:
    assert list(started(tmp_path).agents) == ["codex"]
    assert list(started(tmp_path, *claude_options(tmp_path, key)).agents) == ["codex"]
    missing = ("--claude-key-file", str(tmp_path / "absent"))
    assert list(started(tmp_path, *missing).agents) == ["codex"]


def test_a_claude_key_adds_claude_code(tmp_path: Path) -> None:
    setup = started(tmp_path, *claude_options(tmp_path, "claude-key\n"))
    run = setup.agents["claude_code"].run_in(tmp_path / "project")
    assert isinstance(run, ClaudeRun)
    assert list(setup.agents) == ["codex", "claude_code"]
    assert (run.model, run.api_key, run.workspace_root) == ("claude-model", "claude-key", tmp_path)
    assert "claude-key" not in repr(run)


@pytest.mark.parametrize("codex_key", ["", "two words\n", "chave-inválida\n"])
def test_an_unusable_codex_key_stops_the_start(tmp_path: Path, codex_key: str) -> None:
    with pytest.raises(ValueError, match="Codex API key"):
        started(tmp_path, codex_key=codex_key)


def test_a_claude_key_with_spaces_or_without_its_binary_stops_the_start(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Claude Code API key"):
        started(tmp_path, *claude_options(tmp_path, "two words"))
    claude_options(tmp_path, "claude-key")
    with pytest.raises(ValueError, match="binary and model"):
        started(tmp_path, "--claude-key-file", str(tmp_path / "key"))


def test_the_workspace_root_must_exist(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace root"):
        started(tmp_path, "--workspace-root", str(tmp_path / "absent"))
