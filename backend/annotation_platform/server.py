"""FastAPI entry point for the local annotation platform."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from reid_annotation_tool.project_registry import ProjectRegistry, RegistryError

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


class ProjectSummary(BaseModel):
    config: str
    dataset: str
    exists: bool
    identities: int
    tracks: int
    pairs: int
    rounds: list[str]
    live_round: str
    labelled: int
    pending: int


class ProjectDetail(ProjectListItem):
    summary: ProjectSummary


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


def _configured_origins() -> list[str]:
    raw = os.environ.get("ANNOTATION_CORS_ORIGINS")
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


async def _registry(request: Request) -> ProjectRegistry:
    try:
        return ProjectRegistry.load(request.app.state.registry_path)
    except RegistryError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "registry_invalid", "message": str(error)},
        ) from error


Registry = Annotated[ProjectRegistry, Depends(_registry)]


def create_app(
    registry_path: str | Path | None = None,
    cors_origins: list[str] | tuple[str, ...] | None = None,
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
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=[],
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
        return {**entry.describe(), "summary": entry.project.summary()}

    return application


app = create_app()
