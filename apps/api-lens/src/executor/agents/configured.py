"""The agents this executor was started with."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from src.executor.agents.base import AgentAdapter, AgentRun
from src.shared.executor_protocol import AgentName


@dataclass(frozen=True)
class ConfiguredAgent:
    adapter: AgentAdapter
    run_in: Callable[[Path], AgentRun]


@dataclass(frozen=True)
class ExecutorSetup:
    workspace_root: Path
    state_dir: Path
    agents: Mapping[AgentName, ConfiguredAgent]
