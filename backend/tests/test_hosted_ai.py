from datetime import datetime, timedelta, timezone
import io
import json

import httpx
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from annotation_platform.hosted import create_hosted_app, hosted_task_types, user_workspace
from annotation_platform.img_annotation.common.image_dataset import item_id
from annotation_platform.workspace import Workspace

ORIGIN = "https://annotation.apps.skillsmaster.jp"
TOKEN = "a" * 32
HEADERS = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def service(tmp_path):
    workspace = Workspace(user_workspace(tmp_path, "alice"), hosted_task_types(), import_roots=[])
    entry = workspace.create_project("Labels", "polygon", {"categories": ["cat"]})
    image = io.BytesIO(); Image.new("RGB", (10, 8)).save(image, format="PNG")
    (entry.project.root / "sample.png").write_bytes(image.getvalue())
    state = {"submitted": [], "lostReply": False, "transientStatus": 503, "scope": True,
        "artifact": {"contract_version": 1, "engine": "owlv2", "width": 10, "height": 8,
                     "model_version": "test", "device": "cpu", "candidates": [
            {"class_name": "cat", "confidence": 0.9, "bbox": {"x": 1, "y": 1, "width": 4, "height": 4}}]}}
    def platform(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={"userId": "alice" if request.headers.get("authorization") == "Bearer " + TOKEN else "bob",
                "appId": "annotation", "scopes": ["platform.auth", "platform.ai-runs"] if state["scope"] else ["platform.auth"],
                "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()})
        assert request.headers["authorization"] == "Bearer " + TOKEN
        if request.url.path == "/v1/runs":
            state["submitted"].append((request.headers["idempotency-key"], request.read()))
            if state["lostReply"]:
                state["lostReply"] = False
                return httpx.Response(state["transientStatus"], json={"detail": "Submission pending"})
            return httpx.Response(201, json={"run_id": "owned-run"})
        if request.url.path == "/v1/runs/owned-run": return httpx.Response(200, json={"status": "success"})
        if request.url.path == "/v1/runs/owned-run/result": return httpx.Response(200, json={"content_url": "/v1/runs/owned-run/result/content"})
        if request.url.path == "/v1/runs/owned-run/result/content": return httpx.Response(200, json=state["artifact"])
        raise AssertionError("Unexpected platform route")
    client = httpx.AsyncClient(transport=httpx.MockTransport(platform))
    app = create_hosted_app(tmp_path, ORIGIN, "https://www.skillsmaster.jp", client=client)
    return app, state, workspace, entry, client


def payload():
    return {"id": "stable-job-id", "itemId": item_id("sample.png"), "engine": "owlv2", "confidence": 0.25}


def test_owned_submission_result_approval_and_restart(service):
    app, state, workspace, entry, transport = service
    base = "/api/projects/" + entry.id + "/ai-jobs"
    with TestClient(app, base_url=ORIGIN) as client:
        created = client.post(base, headers=HEADERS, json=payload())
        assert created.status_code == 200, created.text
        assert created.json()["status"] == "running"
        assert client.post(base, headers=HEADERS, json=payload()).json()["runId"] == "owned-run"
        assert len(state["submitted"]) == 1
        key, multipart = state["submitted"][0]
        assert len(key) == 64 and b"annotation_detect" in multipart and b"image/png" in multipart
        assert b"output_format" not in multipart
        assert client.get(base, headers={"Authorization": "Bearer " + "b" * 32}).status_code == 404
        refreshed = client.post(base + "/stable-job-id/refresh", headers=HEADERS, json={})
        assert refreshed.json()["status"] == "ready"
        assert len(refreshed.json()["result"]["polygons"]) == 1
    restarted = create_hosted_app(workspace.root.parent.parent, ORIGIN, "https://www.skillsmaster.jp", client=transport)
    with TestClient(restarted, base_url=ORIGIN) as client:
        assert client.get(base, headers=HEADERS).json()["items"][0]["status"] == "ready"
        assert client.post(base + "/stable-job-id/apply", headers=HEADERS, json={}).json()["status"] == "applied"
        assert client.post(base + "/stable-job-id/apply", headers=HEADERS, json={}).json()["status"] == "applied"
        assert client.post(base + "/stable-job-id/reject", headers=HEADERS, json={}).status_code == 409
        queue = client.get("/api/projects/" + entry.id + "/queue", headers=HEADERS).json()
        assert queue["items"][0]["revision"] == 1


@pytest.mark.parametrize("status", [409, 503])
def test_lost_reply_uses_same_idempotency_key_and_persisted_job(service, status):
    app, state, _, entry, _ = service
    base = "/api/projects/" + entry.id + "/ai-jobs"
    with TestClient(app, base_url=ORIGIN) as client:
        state["lostReply"] = True
        state["transientStatus"] = status
        assert client.post(base, headers=HEADERS, json=payload()).status_code == status
        assert client.get(base, headers=HEADERS).json()["items"][0]["status"] == "submitting"
        assert client.post(base, headers=HEADERS, json=payload()).json()["runId"] == "owned-run"
        assert len(state["submitted"]) == 2
        assert state["submitted"][0][0] == state["submitted"][1][0]
        assert client.post(base, headers=HEADERS, json={**payload(), "confidence": 0.5}).status_code == 409


@pytest.mark.parametrize("failure", ["dimensions", "category", "mask", "scope"])
def test_unsafe_results_and_removed_scopes_are_rejected(service, failure):
    app, state, _, entry, _ = service
    base = "/api/projects/" + entry.id + "/ai-jobs"
    with TestClient(app, base_url=ORIGIN) as client:
        if failure == "scope":
            state["scope"] = False
            assert client.post(base, headers=HEADERS, json=payload()).status_code == 403
            assert not state["submitted"]
            return
        client.post(base, headers=HEADERS, json=payload())
        if failure == "dimensions": state["artifact"]["width"] = 999
        if failure == "category": state["artifact"]["candidates"][0]["class_name"] = "foreign"
        if failure == "mask": state["artifact"]["candidates"][0]["mask"] = {"counts": []}
        assert client.post(base + "/stable-job-id/refresh", headers=HEADERS, json={}).status_code == 502
        assert client.post(base + "/stable-job-id/apply", headers=HEADERS, json={}).status_code == 409


def test_stale_manual_annotation_cannot_be_overwritten(service):
    app, state, _, entry, _ = service
    base = "/api/projects/" + entry.id
    with TestClient(app, base_url=ORIGIN) as client:
        client.post(base + "/ai-jobs", headers=HEADERS, json=payload())
        assert client.post(base + "/annotations", headers=HEADERS, json={"item_id": item_id("sample.png"),
            "result": {"base_revision": 0, "image_size": {"width": 10, "height": 8}, "polygons": []}}).status_code == 200
        client.post(base + "/ai-jobs/stable-job-id/refresh", headers=HEADERS, json={})
        assert client.post(base + "/ai-jobs/stable-job-id/apply", headers=HEADERS, json={}).status_code == 409
        queue = client.get(base + "/queue", headers=HEADERS).json()
        assert queue["items"][0]["polygons"] == [] and queue["items"][0]["revision"] == 1


def test_apply_preserves_manual_polygons_and_recovers_interrupted_status_write(service):
    from annotation_platform.hosted_ai import job_database
    app, _, workspace, entry, _ = service
    base = "/api/projects/" + entry.id
    manual = {"category": "cat", "points": [[6, 1], [9, 1], [9, 5]]}
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post(base + "/annotations", headers=HEADERS, json={"item_id": item_id("sample.png"),
            "result": {"base_revision": 0, "image_size": {"width": 10, "height": 8}, "polygons": [manual]}}).status_code == 200
        assert client.post(base + "/ai-jobs", headers=HEADERS, json=payload()).status_code == 200
        assert client.post(base + "/ai-jobs/stable-job-id/refresh", headers=HEADERS, json={}).status_code == 200
        assert client.post(base + "/ai-jobs/stable-job-id/apply", headers=HEADERS, json={}).status_code == 200
        # Simulate a crash after native annotations committed but before job status did.
        with job_database(workspace.root) as connection:
            connection.execute("UPDATE jobs SET status='ready' WHERE id='stable-job-id'")
        assert client.post(base + "/ai-jobs/stable-job-id/apply", headers=HEADERS, json={}).status_code == 200
        queued = client.get(base + "/queue", headers=HEADERS).json()["items"][0]
        assert queued["revision"] == 2 and len(queued["polygons"]) == 2
        assert queued["polygons"][0] == manual


def test_invalid_engine_value_is_rejected_without_creating_a_job(service):
    app, state, _, entry, _ = service
    base = "/api/projects/" + entry.id + "/ai-jobs"
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.post(base, headers=HEADERS, json={**payload(), "engine": []}).status_code == 422
        assert client.get(base, headers=HEADERS).json()["items"] == [] and not state["submitted"]
