"""FastAPI entry point for the local annotation platform."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal, NoReturn

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .project_forms import ManagementError, describe_spec, task_type_spec
from .project_registry import (
    ProjectRegistry,
    ProjectRegistryError,
    RegisteredProject,
)
from .task_types import (
    MAX_QUEUE_LIMIT,
    ActionRecord,
    ActionRequest,
    QueueRequest,
    Submission,
    TaskActionModule,
    TaskConflictError,
    TaskOperationError,
    TaskTypeRegistry,
    default_task_types,
)
from .workspace import (
    DEFAULT_WORKSPACE,
    WORKSPACE_ENV,
    Workspace,
    extract_archive,
    place,
    spool,
)

# The order the web UI offers task types in when creating a project.
TASK_TYPE_ORDER = (
    "classification",
    "captioning",
    "text_span",
    "detection",
    "segmentation",
    "polygon",
    "depth",
    "reid",
)
# Names the versioned contract a download was validated against.
EXPORT_CONTRACT_HEADER = "X-Export-Contract"
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


class ActionOptions(BaseModel):
    options: dict[str, object] = Field(default_factory=dict)


class ActionResponse(BaseModel):
    id: str
    name: str
    state: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    log: list[str]
    result: dict[str, object] | None
    error: str | None


class ActionListResponse(BaseModel):
    actions: list[str]
    jobs: list[ActionResponse]


class FieldOption(BaseModel):
    value: str
    label: str


class FieldSpec(BaseModel):
    key: str
    label: str
    type: Literal["text", "path", "integer", "number", "boolean", "select", "list"]
    required: bool
    default: Any = None
    options: list[FieldOption] | None
    help: str | None
    lock: Literal["none", "append_only", "locked"]
    server_only: bool
    group: str | None


class ExportFormatInfo(BaseModel):
    format: str
    label: str
    # Versioned export contract, e.g. "detection-coco/v1".
    contract: str


class TaskTypeInfo(BaseModel):
    type: str
    label: str
    description: str
    import_modes: list[Literal["upload", "directory", "managed"]]
    # Extensions (lowercase, with the dot) that `upload` mode accepts.
    upload_extensions: list[str]
    export_formats: list[ExportFormatInfo]
    fields: list[FieldSpec]


class CreateProjectRequest(BaseModel):
    name: str
    task_type: str
    settings: dict[str, Any] = Field(default_factory=dict)


class DataSource(BaseModel):
    mode: Literal["managed", "directory"]
    path: str


class ProjectSettingsResponse(BaseModel):
    id: str
    name: str
    task_type: str
    values: dict[str, Any]
    fields: list[FieldSpec]
    config_path: str
    config_text: str
    data_source: DataSource
    import_roots: list[str]
    annotated: bool


class UpdateSettingsRequest(BaseModel):
    name: str | None = None
    settings: dict[str, Any] | None = None


class ConfigTextRequest(BaseModel):
    text: str


class LinkDirectoryRequest(BaseModel):
    path: str


class ImportFileResponse(BaseModel):
    path: str
    size: int


class ImportArchiveResponse(BaseModel):
    imported: int
    skipped: int


def _configured_origins() -> list[str]:
    raw = os.environ.get("ANNOTATION_CORS_ORIGINS")
    if raw is None:
        return list(DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


async def _registry(request: Request) -> ProjectRegistry:
    try:
        return request.app.state.workspace.load_registry()
    except ProjectRegistryError as error:
        raise HTTPException(
            status_code=500,
            detail={"code": "registry_invalid", "message": str(error)},
        ) from error


Registry = Annotated[ProjectRegistry, Depends(_registry)]


def _workspace(request: Request) -> Workspace:
    return request.app.state.workspace


ProjectWorkspace = Annotated[Workspace, Depends(_workspace)]

# Raw request bodies are read from the stream rather than declared as
# parameters (so nothing is buffered in memory); describe them for OpenAPI.
_BINARY_BODY = {"schema": {"type": "string", "format": "binary"}}
_ERRORS = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
}


def create_app(
    workspace: str | Path | None = None,
    cors_origins: list[str] | tuple[str, ...] | None = None,
    task_types: TaskTypeRegistry | None = None,
    frontend_dist: str | Path | None = None,
    import_roots: Iterable[str | Path] | None = None,
) -> FastAPI:
    """Build the API without loading project data until a request needs it.

    ``workspace`` (or ``ANNOTATION_WORKSPACE``) is the directory holding
    ``projects.yaml`` and the web-created projects; a YAML file path is
    accepted as the registry itself, its directory being the workspace.
    ``import_roots`` (or ``ANNOTATION_IMPORT_ROOTS``) lists the extra server
    directories a project may link as its dataset.
    ``frontend_dist`` (or ``ANNOTATION_FRONTEND_DIST``) points at a Vite build;
    when set, the same process serves the web UI so one port carries both.
    """
    origins = list(cors_origins) if cors_origins is not None else _configured_origins()
    if "*" in origins:
        raise ValueError("CORS origins must be explicit; wildcard '*' is not allowed")

    application = FastAPI(
        title="Annotation Toolkits API",
        version="0.1.0",
        description="Local API for projects and annotation workflows.",
    )
    application.state.task_types = task_types or default_task_types()
    # Constructing the workspace only computes paths: export_openapi.py builds
    # this app, and nothing may be created on disk before the first write.
    application.state.workspace = Workspace(
        workspace
        if workspace is not None
        else os.environ.get(WORKSPACE_ENV, DEFAULT_WORKSPACE),
        application.state.task_types,
        import_roots,
    )
    application.state.registry_path = application.state.workspace.registry_path
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["Content-Disposition", EXPORT_CONTRACT_HEADER],
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
        return _project_detail(_project_entry(registry, project_id))

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
        request: Request,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=MAX_QUEUE_LIMIT)] = 60,
        status: str = "",
        q: str = "",
    ) -> dict:
        """Queue page. ``status`` and ``q`` are the filters every task type
        accepts; any other query parameter (ReID's ``kind``/``split``) is passed
        through as a filter for the task module to accept or reject."""
        entry = _project_entry(registry, project_id)
        filters = {
            key: value
            for key, value in request.query_params.items()
            if key not in {"offset", "limit"} and value
        }
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
        "/api/projects/{project_id}/actions",
        response_model=ActionListResponse,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def list_actions(project_id: str, registry: Registry) -> dict:
        entry = _project_entry(registry, project_id)
        module = _action_module(entry.module)
        return {
            "actions": list(module.action_names()),
            "jobs": [
                _action_response(job) for job in module.list_actions(entry.project)
            ],
        }

    @application.post(
        "/api/projects/{project_id}/actions/{action}",
        response_model=ActionResponse,
        status_code=202,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def start_action(
        project_id: str,
        action: str,
        payload: ActionOptions,
        registry: Registry,
    ) -> dict:
        entry = _project_entry(registry, project_id)
        module = _action_module(entry.module)
        try:
            job = module.start_action(
                entry.project, ActionRequest(name=action, options=payload.options)
            )
        except TaskOperationError as error:
            _raise_task_error(error)
        return _action_response(job)

    @application.get(
        "/api/projects/{project_id}/actions/jobs/{action_id}",
        response_model=ActionResponse,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def get_action(
        project_id: str, action_id: str, registry: Registry
    ) -> dict:
        entry = _project_entry(registry, project_id)
        module = _action_module(entry.module)
        job = module.get_action(entry.project, action_id)
        if job is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "action_not_found", "message": "action job not found"},
            )
        return _action_response(job)

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

    _add_management_routes(application)

    dist = frontend_dist if frontend_dist is not None else os.environ.get("ANNOTATION_FRONTEND_DIST")
    if dist:
        _mount_frontend(application, Path(dist))

    return application


def _mount_frontend(application: FastAPI, dist: Path) -> None:
    """Serve the built SPA after every API route, so it can never shadow one."""
    root = dist.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise ValueError(f"frontend build not found: {index}")

    @application.get("/runtime-config", include_in_schema=False)
    async def standalone_runtime():
        return {"mode": "standalone"}

    @application.get("/{asset_path:path}", include_in_schema=False)
    async def frontend(asset_path: str) -> Response:
        # An unknown /api path is a client bug, not a page: keep the JSON 404
        # instead of answering with index.html and a misleading 200.
        if asset_path == "api" or asset_path.startswith("api/"):
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "API endpoint not found"},
            )
        candidate = (root / asset_path).resolve()
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        # A stale tab asking for a bundle hash from before a redeploy must get
        # a 404, not index.html served as JavaScript.
        if asset_path.startswith("assets/"):
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "asset not found"},
            )
        # Client-side routes (/projects/x/detect) all render from index.html;
        # it must not be cached, or a redeploy keeps loading stale bundles.
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _add_management_routes(application: FastAPI) -> None:
    """Project management: the web UI is the only way projects are administered."""

    @application.get("/api/task-types", response_model=list[TaskTypeInfo])
    async def list_task_types(request: Request) -> list[dict]:
        registered = set(request.app.state.task_types.names())
        specs = (task_type_spec(name) for name in TASK_TYPE_ORDER if name in registered)
        return [describe_spec(spec) for spec in specs if spec is not None]

    @application.post(
        "/api/projects",
        response_model=ProjectDetail,
        status_code=201,
        responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def create_project(payload: CreateProjectRequest, workspace: ProjectWorkspace) -> dict:
        try:
            entry = workspace.create_project(payload.name, payload.task_type, payload.settings)
        except ManagementError as error:
            _raise_management_error(error)
        return _project_detail(entry)

    @application.get(
        "/api/projects/{project_id}/settings",
        response_model=ProjectSettingsResponse,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def get_settings(project_id: str, workspace: ProjectWorkspace) -> dict:
        try:
            return workspace.settings(project_id)
        except ManagementError as error:
            _raise_management_error(error)

    @application.put(
        "/api/projects/{project_id}/settings",
        response_model=ProjectSettingsResponse,
        responses=_ERRORS,
    )
    async def update_settings(
        project_id: str, payload: UpdateSettingsRequest, workspace: ProjectWorkspace
    ) -> dict:
        try:
            return workspace.update_settings(project_id, payload.name, payload.settings)
        except ManagementError as error:
            _raise_management_error(error)

    @application.put(
        "/api/projects/{project_id}/config",
        response_model=ProjectSettingsResponse,
        responses=_ERRORS,
    )
    async def update_config(
        project_id: str, payload: ConfigTextRequest, workspace: ProjectWorkspace
    ) -> dict:
        try:
            return workspace.update_config_text(project_id, payload.text)
        except ManagementError as error:
            _raise_management_error(error)

    @application.delete(
        "/api/projects/{project_id}",
        status_code=204,
        response_class=Response,
        responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    )
    async def delete_project(project_id: str, workspace: ProjectWorkspace) -> Response:
        try:
            workspace.delete_project(project_id)
        except ManagementError as error:
            _raise_management_error(error)
        return Response(status_code=204)

    @application.post(
        "/api/projects/{project_id}/import/files",
        response_model=ImportFileResponse,
        responses=_ERRORS,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": _BINARY_BODY},
            }
        },
    )
    async def import_file(
        project_id: str,
        request: Request,
        workspace: ProjectWorkspace,
        path: Annotated[str, Query(description="dataset-relative image path")],
    ) -> dict:
        try:
            target, relative = workspace.upload_target(project_id, path)
            temporary, size = await spool(request.stream(), target.parent, ".part")
            if size == 0:
                temporary.unlink(missing_ok=True)
                raise ManagementError("empty_file", "the uploaded file is empty")
            place(temporary, target)
        except ManagementError as error:
            _raise_management_error(error)
        return {"path": relative, "size": size}

    @application.post(
        "/api/projects/{project_id}/import/archive",
        response_model=ImportArchiveResponse,
        responses=_ERRORS,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/zip": _BINARY_BODY},
            }
        },
    )
    async def import_archive(
        project_id: str, request: Request, workspace: ProjectWorkspace
    ) -> dict:
        try:
            root = workspace.upload_root(project_id)
            suffixes = workspace.upload_suffixes(project_id)
            # Spooled next to (not inside) the dataset, then extracted off the
            # event loop: a large archive must not stall every other request.
            archive, _ = await spool(request.stream(), root.parent, ".zip")
            try:
                imported, skipped = await run_in_threadpool(
                    extract_archive, archive, root, suffixes
                )
            finally:
                archive.unlink(missing_ok=True)
        except ManagementError as error:
            _raise_management_error(error)
        return {"imported": imported, "skipped": skipped}

    @application.post(
        "/api/projects/{project_id}/import/directory",
        response_model=ProjectSettingsResponse,
        responses=_ERRORS,
    )
    async def link_directory(
        project_id: str, payload: LinkDirectoryRequest, workspace: ProjectWorkspace
    ) -> dict:
        try:
            return workspace.link_directory(project_id, payload.path)
        except ManagementError as error:
            _raise_management_error(error)

    @application.get(
        "/api/projects/{project_id}/export",
        response_class=FileResponse,
        responses={
            200: {
                "description": "The exported file, or a zip when the export has several files.",
                "content": {"application/octet-stream": _BINARY_BODY},
            },
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
        },
    )
    async def export_project(
        project_id: str,
        workspace: ProjectWorkspace,
        export_format: Annotated[str, Query(alias="format")] = "native",
    ) -> FileResponse:
        try:
            download = await run_in_threadpool(workspace.export, project_id, export_format)
        except ManagementError as error:
            _raise_management_error(error)
        cleanup = (
            BackgroundTask(download.path.unlink, missing_ok=True) if download.temporary else None
        )
        return FileResponse(
            download.path,
            filename=download.filename,
            background=cleanup,
            headers={EXPORT_CONTRACT_HEADER: download.contract},
        )


def _project_detail(entry: RegisteredProject) -> dict:
    task_status = entry.module.status(entry.project)
    return {**entry.describe(task_status), "summary": task_status.details}


def _raise_management_error(error: ManagementError) -> NoReturn:
    raise HTTPException(
        status_code=error.status,
        detail={"code": error.code, "message": str(error)},
    ) from error


def _project_entry(registry: ProjectRegistry, project_id: str):
    try:
        return registry.get_entry(project_id)
    except ProjectRegistryError as error:
        raise HTTPException(
            status_code=404,
            detail={"code": "project_not_found", "message": str(error)},
        ) from error


def _raise_task_error(error: TaskOperationError) -> None:
    raise HTTPException(
        status_code=409 if isinstance(error, TaskConflictError) else 422,
        detail={"code": error.code, "message": str(error)},
    ) from error


def _action_module(module: object) -> TaskActionModule:
    if not isinstance(module, TaskActionModule):
        raise HTTPException(
            status_code=404,
            detail={
                "code": "actions_not_supported",
                "message": "project task type does not expose actions",
            },
        )
    return module


def _action_response(job: ActionRecord) -> dict:
    return {
        "id": job.id,
        "name": job.name,
        "state": job.state,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "log": list(job.log),
        "result": dict(job.result) if job.result is not None else None,
        "error": job.error,
    }


app = create_app()
