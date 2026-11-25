"""Compose the Agent Lens web API."""

from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.api.observability import observed_app
from src.api.routers.folders import router as folders_router
from src.api.routers.run_history import router as run_history_router
from src.api.routers.runs import router as runs_router
from src.api.routers.threads import router as threads_router

app = observed_app(
    "Agent Lens", "agent-lens-backend",
    {("POST", "/runs"): "run.sse"},
)
# The API has no authentication. Rejecting other host names blocks DNS rebinding from web pages.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.include_router(folders_router)
app.include_router(runs_router)
app.include_router(run_history_router)
app.include_router(threads_router)
