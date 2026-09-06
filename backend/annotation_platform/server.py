"""FastAPI entry point for the local annotation platform."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from reid_annotation_tool.project_registry import ProjectRegistry, RegistryError

from .task_types import (
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
    TaskTypeRegistry,
    default_task_types,
)

DEFAULT_REGISTRY_PATH = "projects.yaml"
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)


class ProjectListItem(BaseModel):
    id: str
    name: str
    task_type: str
    root: str
    status: str


class ProjectDetail(ProjectListItem):
    summary: dict[str, object]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


class QueueResponse(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[dict[str, object]]


class AnnotationRequest(BaseModel):
    item_id: str = Field(min_length=1)
    result: dict[str, object]


class TaskStatusResponse(BaseModel):
    state: str
    details: dict[str, object]


class AnnotationResponse(BaseModel):
    item: dict[str, object]
    status: TaskStatusResponse


def _configured_origins() -> list[str]:
    raw = os.environ.get("ANNOTATION_CORS_ORIGINS")
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


async def _registry(request: Request) -> ProjectRegistry:
    try:
        return ProjectRegistry.load(
            request.app.state.registry_path, request.app.state.task_types
        )
    except RegistryError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "registry_invalid", "message": str(error)},
        ) from error


Registry = Annotated[ProjectRegistry, Depends(_registry)]


def create_app(
    registry_path: str | Path | None = None,
    cors_origins: list[str] | tuple[str, ...] | None = None,
    task_types: TaskTypeRegistry | None = None,
) -> FastAPI:
    """Build the API without loading project data until a request needs it."""
    origins = list(cors_origins) if cors_origins is not None else _configured_origins()
    if "*" in origins:
        raise ValueError("CORS origins must be explicit; wildcard '*' is not allowed")

    application = FastAPI(
        title="Annotation Toolkits API",
        version="0.1.0",
        description="Local API for projects and annotation workflows.",
    )
    application.state.registry_path = Path(
        registry_path
        if registry_path is not None
        else os.environ.get("ANNOTATION_PROJECTS_CONFIG", DEFAULT_REGISTRY_PATH)
    )
    application.state.task_types = task_types or default_task_types()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @application.get(
        "/api/projects",
        response_model=list[ProjectListItem],
        responses={500: {"model": ErrorResponse}},
    )
    async def list_projects(registry: Registry) -> list[dict]:
        return registry.list_projects()

    @application.get(
        "/api/projects/{project_id}",
        response_model=ProjectDetail,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def get_project(project_id: str, registry: Registry) -> dict:
        try:
            entry = registry.get_entry(project_id)
        except RegistryError as error:
            raise HTTPException(
                status_code=404,
                detail={"code": "project_not_found", "message": str(error)},
            ) from error
        task_status = entry.module.status(entry.project)
        return {
            "id": entry.id,
            "name": entry.name,
            "task_type": entry.task_type,
            "root": str(entry.project.root),
            "status": task_status.state,
            "summary": task_status.details,
        }

    @application.get(
        "/api/projects/{project_id}/queue",
        response_model=QueueResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def get_queue(
        project_id: str,
        registry: Registry,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=200)] = 60,
        kind: str = "",
        split: str = "",
        status: str = "",
        q: str = "",
    ) -> dict:
        entry = _project_entry(registry, project_id)
        raw_filters = {"kind": kind, "split": split, "status": status, "q": q}
        filters = {key: value for key, value in raw_filters.items() if value}
        try:
            page = entry.module.queue(
                entry.project,
                QueueRequest(offset=offset, limit=limit, filters=filters),
            )
        except TaskOperationError as error:
            _raise_task_error(error)
        return {
            "total": page.total,
            "offset": page.offset,
            "limit": page.limit,
            "items": list(page.items),
        }

    @application.post(
        "/api/projects/{project_id}/annotations",
        response_model=AnnotationResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def submit_annotation(
        project_id: str, annotation: AnnotationRequest, registry: Registry
    ) -> dict:
        entry = _project_entry(registry, project_id)
        try:
            result = entry.module.submit(
                entry.project,
                Submission(annotation.item_id, annotation.result),
            )
        except TaskOperationError as error:
            _raise_task_error(error)
        return {
            "item": dict(result.item),
            "status": {
                "state": result.status.state,
                "details": dict(result.status.details),
            },
        }

    @application.get(
        "/api/projects/{project_id}/files/{file_path:path}",
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def get_project_file(
        project_id: str, file_path: str, registry: Registry
    ) -> Response:
        entry = _project_entry(registry, project_id)
        root = entry.project.root.resolve()
        candidate = (root / file_path.lstrip("/")).resolve()
        if (root not in candidate.parents and candidate != root) or not candidate.is_file():
            raise HTTPException(
                status_code=404,
                detail={"code": "file_not_found", "message": "project file not found"},
            )
        try:
            content = candidate.read_bytes()
        except OSError:
            raise HTTPException(
                status_code=404,
                detail={"code": "file_not_found", "message": "project file not found"},
            ) from None
        media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        return Response(content=content, media_type=media_type)

    return application


def _project_entry(registry: ProjectRegistry, project_id: str):
    try:
        return registry.get_entry(project_id)
    except RegistryError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "project_not_found", "message": str(error)},
        ) from error


def _raise_task_error(error: TaskOperationError) -> None:
    raise HTTPException(
        status_code=409 if isinstance(error, TaskConflictError) else 422,
        detail={"code": error.code, "message": str(error)},
    ) from error


app = create_app()
