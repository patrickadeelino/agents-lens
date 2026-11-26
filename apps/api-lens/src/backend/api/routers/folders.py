"""Let the browser choose a folder below the executor's workspace root."""

from collections.abc import Callable
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Query

from src.backend.api.dependencies import executor_socket
from src.backend.infrastructure.executor_client import ExecutorClient
from src.shared.executor_protocol import (
    MAX_FOLDER_PATH,
    FolderListing,
    FolderRequest,
    WorkspaceSelection,
)

router = APIRouter()


def ask_executor[Result](request: Callable[[ExecutorClient], Result]) -> Result:
    try:
        return request(ExecutorClient(executor_socket()))
    except httpx.HTTPStatusError as error:
        status = error.response.status_code
        if status != 409:
            raise HTTPException(status_code=503, detail="Folder list is unavailable. Restart the executor.") from error
        raise HTTPException(status_code=409, detail=error.response.json().get("detail")) from error
    except (httpx.RequestError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Executor unavailable.") from error


@router.get("/folders")
def list_folders(path: Annotated[str, Query(max_length=MAX_FOLDER_PATH)] = "") -> FolderListing:
    return ask_executor(lambda executor: executor.list_folders(path))


@router.post("/folders")
def select_folder(
    folder: FolderRequest, x_agent_lens: Annotated[Literal["folder-selection"], Header()],
) -> WorkspaceSelection:
    return ask_executor(lambda executor: executor.select_folder(folder.path))
