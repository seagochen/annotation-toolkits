"""Application-owned AI jobs; the platform sees only scoped generic run requests."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
from pathlib import Path
import re
import sqlite3

from fastapi import HTTPException, Request
from PIL import Image
from starlette.responses import JSONResponse

from .img_annotation.standard.polygon_task import PolygonStore, _polygons
from .task_types import Submission, TaskConflictError, TaskOperationError
from .project_registry import ProjectRegistryError

JOB_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
MAX_INPUT_BYTES = 50 * 1024 * 1024


@contextmanager
def job_database(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / "ai-jobs.sqlite", timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_json TEXT NOT NULL,
            source_sha256 TEXT NOT NULL, source_revision INTEGER NOT NULL, image_size_json TEXT NOT NULL,
            source_polygons_json TEXT NOT NULL, run_id TEXT, status TEXT NOT NULL, result_json TEXT, created_at TEXT NOT NULL
        )""")
        yield connection
        connection.commit()
    finally:
        connection.close()


def _view(row) -> dict:
    return {"id": row["id"], "projectId": row["project_id"], "request": json.loads(row["request_json"]),
            "sourceRevision": row["source_revision"], "imageSize": json.loads(row["image_size_json"]),
            "runId": row["run_id"], "status": row["status"],
            "result": None if row["result_json"] is None else json.loads(row["result_json"]), "createdAt": row["created_at"]}


def platform_json(response) -> dict:
    try:
        result = response.json()
    except ValueError as error:
        raise HTTPException(502, "Invalid platform AI response") from error
    if not isinstance(result, dict): raise HTTPException(502, "Invalid platform AI response")
    return result


class HostedAi:
    def __init__(self, identity):
        self.identity = identity

    async def route(self, request: Request, owner: dict, workspace) -> JSONResponse:
        if "platform.ai-runs" not in owner["scopes"]:
            raise HTTPException(403, "AI capability is not granted")
        parts = request.url.path.split("/")
        project_id = parts[3]
        try:
            entry = workspace.load_registry().get_entry(project_id)
        except ProjectRegistryError as error:
            raise HTTPException(404, "Project not found") from error
        if entry.task_type != "polygon":
            raise HTTPException(422, "AI suggestions currently require a polygon project")
        if len(parts) == 5 and request.method == "GET":
            with job_database(workspace.root) as connection:
                rows = connection.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 100", (project_id,)).fetchall()
            return JSONResponse({"items": [_view(row) for row in rows]})
        if len(parts) == 5 and request.method == "POST":
            return JSONResponse(await self.submit(request, owner, workspace, entry))
        if len(parts) == 7 and request.method == "POST" and parts[6] in {"refresh", "apply", "reject"}:
            job_id = parts[5]
            with job_database(workspace.root) as connection:
                row = connection.execute("SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)).fetchone()
            if row is None: raise HTTPException(404, "AI job not found")
            if parts[6] == "refresh": return JSONResponse(await self.refresh(owner, workspace, entry, row))
            if parts[6] == "reject":
                with job_database(workspace.root) as connection:
                    if row["status"] not in {"ready", "rejected"}:
                        raise HTTPException(409, "AI job cannot be rejected in its current state")
                    connection.execute("UPDATE jobs SET status='rejected' WHERE id=? AND status='ready'", (job_id,))
                return JSONResponse({"id": job_id, "status": "rejected"})
            return JSONResponse(self.apply(workspace, entry, row))
        raise HTTPException(404, "AI endpoint not found")

    async def submit(self, request: Request, owner: dict, workspace, entry) -> dict:
        try:
            body = await request.json()
        except ValueError as error:
            raise HTTPException(400, "Invalid AI request") from error
        if (not isinstance(body, dict) or set(body) != {"id", "itemId", "engine", "confidence"}
            or not isinstance(body["id"], str) or not JOB_ID.fullmatch(body["id"])
            or not isinstance(body["itemId"], str) or not isinstance(body["engine"], str)
            or body["engine"] not in {"owlv2", "rfdetr-detect"}
            or isinstance(body["confidence"], bool) or not isinstance(body["confidence"], (int, float))
            or not 0 <= body["confidence"] <= 1):
            raise HTTPException(422, "Invalid AI parameters")
        native = PolygonStore(entry.project.value)
        try:
            relative = native.image_path(body["itemId"])
        except TaskOperationError as error:
            raise HTTPException(404, "Image not found") from error
        file = entry.project.root / relative
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise HTTPException(413, "AI image exceeds the size limit")
        try:
            with Image.open(file) as image:
                size = {"width": image.width, "height": image.height}
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise HTTPException(422, "AI input must be a valid image") from error
        if max(size.values()) > 16000 or size["width"] * size["height"] > 60000000:
            raise HTTPException(413, "AI image exceeds the pixel limit")
        pixels = file.read_bytes()
        checksum = hashlib.sha256(pixels).hexdigest()
        payload = json.dumps({**body, "classes": list(entry.project.value.categories)}, sort_keys=True)
        with native.lock:
            current = native._read()["items"].get(body["itemId"])
            revision = 0 if current is None else current["revision"]
        with job_database(workspace.root) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT * FROM jobs WHERE id=?", (body["id"],)).fetchone()
            if existing is not None:
                if existing["project_id"] != entry.id or existing["request_json"] != payload or existing["source_sha256"] != checksum:
                    raise HTTPException(409, "AI job ID already describes another input")
                if existing["run_id"] or existing["status"] != "submitting": return _view(existing)
            else:
                connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,NULL,'submitting',NULL,?)",
                    (body["id"], entry.id, payload, checksum, revision, json.dumps(size),
                     json.dumps([] if current is None else current["polygons"]), datetime.now(timezone.utc).isoformat()))
        # The stable id survives a lost reply or restart. The platform/backend
        # idempotency contract resolves retries without charging another run.
        key = hashlib.sha256((owner["userId"] + ":" + entry.id + ":" + body["id"]).encode()).hexdigest()
        response = await self.identity.platform("/v1/runs", method="POST",
            headers={"Authorization": "Bearer " + owner["token"], "Idempotency-Key": key},
            data={"mode": "annotation_detect", "annotation_engine": body["engine"],
                  "annotation_classes": json.dumps(list(entry.project.value.categories)), "annotation_confidence": str(body["confidence"])},
            files={"file": (file.name, pixels, mimetypes.guess_type(file.name)[0] or "application/octet-stream")})
        result = platform_json(response)
        if not response.is_success:
            if 400 <= response.status_code < 500 and response.status_code not in {409, 429}:
                with job_database(workspace.root) as connection:
                    connection.execute("UPDATE jobs SET status='failed' WHERE id=? AND run_id IS NULL", (body["id"],))
            raise HTTPException(response.status_code, "Platform AI submission failed")
        run_id = result.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise HTTPException(502, "Invalid platform AI response")
        with job_database(workspace.root) as connection:
            connection.execute("UPDATE jobs SET run_id=?,status='running' WHERE id=? AND run_id IS NULL", (run_id, body["id"]))
            saved = connection.execute("SELECT * FROM jobs WHERE id=?", (body["id"],)).fetchone()
        return _view(saved)

    async def refresh(self, owner: dict, workspace, entry, row) -> dict:
        if row["status"] != "running": return _view(row)
        headers = {"Authorization": "Bearer " + owner["token"]}
        reply = await self.identity.platform("/v1/runs/" + row["run_id"], headers=headers)
        if not reply.is_success: raise HTTPException(reply.status_code, "AI run unavailable")
        status = platform_json(reply).get("status")
        if status not in {"success", "failed", "error", "cancelled"}: return _view(row)
        result = None
        if status == "success":
            reply = await self.identity.platform("/v1/runs/" + row["run_id"] + "/result", headers=headers)
            if not reply.is_success: raise HTTPException(reply.status_code, "AI result delivery unavailable")
            reply = await self.identity.platform("/v1/runs/" + row["run_id"] + "/result/content", headers=headers)
            if not reply.is_success: raise HTTPException(reply.status_code, "AI result unavailable")
            result = self.validate_result(platform_json(reply), entry, row)
        with job_database(workspace.root) as connection:
            connection.execute("UPDATE jobs SET status=?,result_json=? WHERE id=? AND status='running'",
                ("ready" if result is not None else "failed", None if result is None else json.dumps(result), row["id"]))
            saved = connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return _view(saved)


    def validate_result(self, result, entry, row) -> dict:
        size = json.loads(row["image_size_json"])
        request = json.loads(row["request_json"])
        if (not isinstance(result, dict) or result.get("contract_version") != 1 or result.get("engine") != request["engine"]
            or result.get("width") != size["width"] or result.get("height") != size["height"]
            or not isinstance(result.get("candidates"), list) or len(result["candidates"]) > 10000):
            raise HTTPException(502, "Invalid annotation result")
        polygons = []
        for candidate in result["candidates"]:
            if not isinstance(candidate, dict) or candidate.get("mask") is not None:
                raise HTTPException(502, "Unsupported annotation result")
            box = candidate.get("bbox")
            confidence = candidate.get("confidence")
            if (not isinstance(box, dict) or set(box) != {"x", "y", "width", "height"}
                or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1):
                raise HTTPException(502, "Invalid annotation candidate")
            x, y, width, height = (box[key] for key in ("x", "y", "width", "height"))
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (x, y, width, height)) or width <= 0 or height <= 0:
                raise HTTPException(502, "Invalid annotation candidate geometry")
            polygons.append({"category": candidate.get("class_name"), "points": [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]})
        try: normalized = _polygons(polygons, entry.project.value.categories, size)
        except TaskOperationError as error: raise HTTPException(502, "Invalid annotation geometry or category") from error
        return {"artifact": result, "polygons": normalized}

    def apply(self, workspace, entry, row) -> dict:
        if row["status"] == "applied": return _view(row)
        if row["status"] != "ready": raise HTTPException(409, "AI suggestions are not ready")
        native = PolygonStore(entry.project.value)
        request = json.loads(row["request_json"])
        relative = native.image_path(request["itemId"])
        if hashlib.sha256((entry.project.root / relative).read_bytes()).hexdigest() != row["source_sha256"]:
            raise HTTPException(409, "Source image changed")
        result = json.loads(row["result_json"])
        try:
            native.submit(Submission(request["itemId"], {"base_revision": row["source_revision"],
                "image_size": json.loads(row["image_size_json"]),
                "polygons": json.loads(row["source_polygons_json"]) + result["polygons"]}))
        except TaskConflictError as error: raise HTTPException(409, "Annotation changed; review the current image before applying AI") from error
        except TaskOperationError as error: raise HTTPException(422, str(error)) from error
        with job_database(workspace.root) as connection:
            connection.execute("UPDATE jobs SET status='applied' WHERE id=? AND status='ready'", (row["id"],))
            saved = connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
        return _view(saved)
