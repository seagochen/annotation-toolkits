"""Multi-user HTTP host; each request receives an isolated application workspace."""
from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
import httpx
from starlette.responses import JSONResponse, Response

from .hosted_identity import HostedIdentity
from .hosted_ai import HostedAi
from .server import _mount_frontend, create_app
from .task_types import TaskTypeRegistry, default_task_types

HOSTED_TASK_TYPES = ("classification", "captioning", "text_span", "detection", "segmentation", "polygon")


def hosted_task_types() -> TaskTypeRegistry:
    registry = default_task_types()
    # ReID/depth can execute local pipelines or read operator model paths. They
    # require a separately approved hosted runtime instead of tenant settings.
    return TaskTypeRegistry(tuple(registry.require(name) for name in HOSTED_TASK_TYPES))


def user_workspace(root: Path, user_id: str) -> Path:
    return root / "users" / hashlib.sha256(user_id.encode()).hexdigest()


def _origin(value: str, *, platform: bool) -> str:
    parsed = urlsplit(value)
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    host = parsed.hostname or ""
    approved = host in {"www.skillsmaster.jp", "api.skillsmaster.jp"} if platform else (
        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.apps\.skillsmaster\.jp", host) is not None
    )
    if (parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
        or not (local or (parsed.scheme == "https" and parsed.port is None and approved))):
        raise ValueError("Invalid hosted origin")
    return value


def create_hosted_app(
    data_root: str | Path | None = None, public_origin: str | None = None,
    platform_origin: str | None = None, frontend_dist: str | Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    root = Path(data_root or os.environ.get("WEB_APP_DATA_DIR", "/data")).resolve()
    public = _origin(public_origin or os.environ["WEB_APP_PUBLIC_ORIGIN"], platform=False)
    platform = _origin(platform_origin or os.environ["SKILLSMASTER_API_BASE_URL"], platform=True)
    owns_client = client is None
    transport = client if client is not None else httpx.AsyncClient(trust_env=False)
    identity = HostedIdentity(public, platform, transport)
    ai = HostedAi(identity)

    @asynccontextmanager
    async def lifespan(app):
        yield
        if owns_client:
            await transport.aclose()

    app = FastAPI(title="Hosted Annotation", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.identity = identity
    task_types = hosted_task_types()

    @app.middleware("http")
    async def dispatch(request: Request, call_next):
        path = request.url.path
        try:
            if path == "/api" or path.startswith("/api/"):
                if request.method not in {"GET", "HEAD"}:
                    identity.assert_write_origin(request)
                owner = await identity.authenticate(request)
                if len(path.split("/")) >= 5 and path.split("/")[2] == "projects" and path.split("/")[4] == "ai-jobs":
                    from .workspace import Workspace
                    workspace = Workspace(user_workspace(root, owner["userId"]), task_types, import_roots=[])
                    response = await ai.route(request, owner, workspace)
                    return _private_response(response)
                if path.startswith("/api/legacy-history/") and request.method == "GET":
                    project_id = path.removeprefix("/api/legacy-history/")
                    file = user_workspace(root, owner["userId"]) / "legacy-records.json"
                    if not file.is_file():
                        raise HTTPException(404, "No legacy history")
                    records = json.loads(file.read_text(encoding="utf8"))
                    datasets = [row for row in records["datasets"] if row["id"] == project_id]
                    if not datasets:
                        raise HTTPException(404, "No legacy history")
                    response = JSONResponse({"dataset": datasets[0], **{key: [row for row in records[key]
                        if row["dataset_id"] == project_id] for key in
                        ("labels", "assets", "tasks", "suggestion_jobs", "suggestions")}})
                    return _private_response(response)
                # No registry or FastAPI state is shared between different users.
                tenant = create_app(user_workspace(root, owner["userId"]), cors_origins=[],
                                    task_types=task_types, import_roots=[])
                # A hosted tenant cannot link server directories even inside its
                # own workspace; this also keeps migration archives out of assets.
                if path.endswith("/import/directory"):
                    raise HTTPException(403, "Hosted projects accept uploaded files only")
                # Delegate ASGI directly, preserving streaming uploads/downloads.
                response = _TenantResponse(tenant, request.scope)
            else:
                response = await call_next(request)
        except HTTPException as error:
            response = JSONResponse({"detail": error.detail}, status_code=error.status_code)
        return _private_response(response)

    app.add_api_route("/auth/platform/login", identity.login, methods=["GET"])
    app.add_api_route("/auth/platform/callback", identity.callback, methods=["GET"])
    app.add_api_route("/auth/platform/me", identity.me, methods=["GET"])
    app.add_api_route("/auth/platform/logout", identity.logout, methods=["POST"])

    @app.get("/healthz")
    async def health():
        return {"ok": True, "mode": "hosted"}

    @app.get("/runtime-config")
    async def runtime_config():
        return {"mode": "hosted", "platformOrigin": platform}

    dist = frontend_dist or os.environ.get("ANNOTATION_FRONTEND_DIST")
    if dist is not None:
        _mount_frontend(app, Path(dist))
    return app


def _private_response(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return response


class _TenantResponse(Response):
    """Pass the response body through without buffering uploaded files."""
    def __init__(self, tenant, scope):
        super().__init__()
        self.tenant, self.tenant_scope = tenant, dict(scope)

    async def __call__(self, scope, receive, send):
        async def forward(message):
            if message["type"] == "http.response.start":
                names = {name.lower() for name, _ in self.raw_headers}
                # Tenant owns content headers; outer security/cache headers win.
                extra = [(name, value) for name, value in self.raw_headers if name.lower() != b"content-length"]
                message = {**message, "headers": [(name, value) for name, value in message["headers"]
                    if name.lower() not in names or name.lower() == b"content-length"] + extra}
            await send(message)
        await self.tenant(self.tenant_scope, receive, forward)
