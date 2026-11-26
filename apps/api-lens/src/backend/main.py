"""Compose the Agent Lens web API."""

from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.backend.api.routers.agents import router as agents_router
from src.backend.api.routers.folders import router as folders_router
from src.backend.api.routers.run_history import router as run_history_router
from src.backend.api.routers.runs import router as runs_router
from src.backend.api.routers.threads import router as threads_router
from src.backend.startup import recover_history
from src.shared.observed_app import observed_app

app = observed_app(
    "Agent Lens", "agent-lens-backend",
    {("POST", "/runs"): "run.sse"},
    prepare=recover_history,
)
# The API has no authentication. Rejecting other host names blocks DNS rebinding from web pages.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.include_router(agents_router)
app.include_router(folders_router)
app.include_router(runs_router)
app.include_router(run_history_router)
app.include_router(threads_router)
