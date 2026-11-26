"""Read how the executor was started and configure its agents."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from src.executor.agents.claude.adapter import ClaudeConfig, claude_agent
from src.executor.agents.codex.adapter import codex_agent
from src.executor.agents.codex.catalog_run import ExecutorConfig
from src.executor.agents.configured import ExecutorSetup


def executor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the host executor on its private Unix socket.")
    parser.add_argument("--codex-bin", required=True, type=Path)
    parser.add_argument("--model", required=True, help="Model Codex uses.")
    parser.add_argument("--claude-bin", type=Path)
    parser.add_argument("--claude-model")
    parser.add_argument("--claude-key-file", type=Path, help="Claude Code is offered when this file holds a key.")
    parser.add_argument("--data-dir", required=True, type=Path, help="Private agent state and the API socket.")
    parser.add_argument(
        "--workspace-root", required=True, type=Path, help="Only this folder and its subfolders can be chosen.",
    )
    return parser


def checked_key(raw: str, agent: str) -> str:
    key = raw.strip()
    if not key or not key.isascii() or any(char.isspace() for char in key):
        raise ValueError(f"Provide the {agent} API key as a single word.")
    return key


def optional_key(path: Path | None, agent: str) -> str | None:
    """Return None when the file is absent or empty, so the agent is simply not offered."""
    if path is None or not path.is_file():
        return None
    raw = path.read_text(encoding="utf-8")
    return checked_key(raw, agent) if raw.strip() else None


def executor_setup(args: argparse.Namespace, codex_input: Sequence[str], state_dir: Path) -> ExecutorSetup:
    """Configure Codex, whose key arrives on standard input, and Claude Code when it has a key."""
    workspace_root = args.workspace_root.resolve()
    if not workspace_root.is_dir():
        raise ValueError("The workspace root must be an existing folder.")
    codex = codex_agent(ExecutorConfig(
        binary=args.codex_bin.resolve(strict=True), state_dir=state_dir, model=args.model,
        api_key=checked_key("".join(codex_input), "Codex"), workspace_root=workspace_root,
    ))
    setup = ExecutorSetup(workspace_root, state_dir, {"codex": codex})
    claude_key = optional_key(args.claude_key_file, "Claude Code")
    if claude_key is None:
        return setup
    if args.claude_bin is None or not args.claude_model:
        raise ValueError("Claude Code needs its binary and model.")
    claude = claude_agent(ClaudeConfig(
        binary=args.claude_bin.resolve(strict=True), state_dir=state_dir, model=args.claude_model,
        api_key=claude_key, workspace_root=workspace_root,
    ))
    return ExecutorSetup(workspace_root, state_dir, {**setup.agents, "claude_code": claude})
