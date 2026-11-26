"""Tell the browser which agents the executor can run."""

import httpx
from fastapi import APIRouter, HTTPException

from src.backend.api.dependencies import executor_client
from src.shared.executor_protocol import AvailableAgents

router = APIRouter()


@router.get("/agents")
def list_agents() -> AvailableAgents:
    try:
        return AvailableAgents(agents=executor_client().available_agents())
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Executor unavailable.") from error
