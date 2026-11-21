"""Compose the Agent Lens web API."""

from fastapi import FastAPI
from fastapi.responses import FileResponse

from src.api.dependencies import PROJECT_DIR
from src.api.routers.replay import router as replay_router
from src.api.routers.runs import router as runs_router

app = FastAPI(title="Agent Lens")
app.include_router(replay_router)
app.include_router(runs_router)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(PROJECT_DIR / "apps" / "web" / "index.html", media_type="text/html")
