"""Compose the Agent Lens web API."""

from src.api.observability import observed_app
from src.api.routers.replay import router as replay_router
from src.api.routers.run_history import router as run_history_router
from src.api.routers.runs import router as runs_router

app = observed_app(
    "Agent Lens", "agent-lens-backend",
    {("POST", "/runs"): "run.sse", ("GET", "/events"): "replay.sse"},
)
app.include_router(replay_router)
app.include_router(runs_router)
app.include_router(run_history_router)
