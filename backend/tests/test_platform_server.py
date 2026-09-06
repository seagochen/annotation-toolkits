import asyncio
from pathlib import Path

import httpx
import pytest

from annotation_platform.server import create_app
from annotation_platform.task_types import (
    ExportRequest,
    ExportResult,
    QueuePage,
    QueueRequest,
    Submission,
    SubmissionResult,
    TaskProject,
    TaskStatus,
    TaskTypeRegistry,
)
from conftest import CANDIDATE_FIELDS, candidate, write_csv
from reid_annotation_tool.core import read_csv


def make_registry(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "identities.csv").write_text("identity_id\nperson-1\n", encoding="utf-8")
    (tmp_path / "reid.yaml").write_text(
        "dataset: ./dataset\npipeline:\n  script: tracking_csv\n",
        encoding="utf-8",
    )
    registry = tmp_path / "projects.yaml"
    registry.write_text(
        "projects:\n"
        "  - id: lobby\n"
        "    name: Lobby\n"
        "    task_type: reid\n"
        "    config: ./reid.yaml\n",
        encoding="utf-8",
    )
    return registry


def make_review_registry(tmp_path: Path) -> tuple[Path, Path]:
    registry = make_registry(tmp_path)
    dataset = tmp_path / "dataset"
    identities = dataset / "identities.csv"
    identities.write_text(
        "img_path,person_id,split,video,track_id,class_id,timestamp\n"
        "images/train/a/00.jpg,a,train,v0.mp4,1,0,0\n"
        "images/train/b/00.jpg,b,train,v0.mp4,2,0,1\n",
        encoding="utf-8",
    )
    for identity in ("a", "b"):
        image = dataset / "images" / "train" / identity / "00.jpg"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"jpeg")
    review = dataset / "review" / "v1" / "candidates.csv"
    write_csv(review, CANDIDATE_FIELDS, [candidate("c1", "a", "b")])
    (dataset / "pairs.csv").write_text(
        "img1,img2,label,split,evidence,person_id1,person_id2,gap_sec\n",
        encoding="utf-8",
    )
    return registry, review


def request(app, method: str, path: str, **kwargs) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(send())


def test_project_list_and_detail_reuse_registry_status(tmp_path):
    app = create_app(make_registry(tmp_path))

    listing = request(app, "GET", "/api/projects")
    assert listing.status_code == 200
    assert listing.json() == [
        {
            "id": "lobby",
            "name": "Lobby",
            "task_type": "reid",
            "root": str(tmp_path / "dataset"),
            "status": "needs_mining",
        }
    ]

    detail = request(app, "GET", "/api/projects/lobby")
    assert detail.status_code == 200
    assert detail.json()["summary"] == {
        "config": str(tmp_path / "reid.yaml"),
        "dataset": str(tmp_path / "dataset"),
        "exists": True,
        "identities": 1,
        "tracks": 0,
        "pairs": 0,
        "rounds": [],
        "live_round": "",
        "labelled": 0,
        "pending": 0,
    }


def test_registry_is_reloaded_between_requests(tmp_path):
    registry = make_registry(tmp_path)
    app = create_app(registry)
    assert request(app, "GET", "/api/projects").json()[0]["name"] == "Lobby"

    registry.write_text(registry.read_text().replace("Lobby", "Entrance"), encoding="utf-8")
    assert request(app, "GET", "/api/projects").json()[0]["name"] == "Entrance"


def test_missing_project_and_invalid_registry_have_stable_errors(tmp_path):
    response = request(create_app(make_registry(tmp_path)), "GET", "/api/projects/missing")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "project_not_found"

    invalid = request(create_app(tmp_path / "absent.yaml"), "GET", "/api/projects")
    assert invalid.status_code == 500
    assert invalid.json()["detail"]["code"] == "registry_invalid"


def test_docs_and_openapi_describe_project_endpoints(tmp_path):
    app = create_app(make_registry(tmp_path))
    assert request(app, "GET", "/docs").status_code == 200
    schema = request(app, "GET", "/openapi.json").json()
    assert schema["info"]["title"] == "Annotation Toolkits API"
    assert set(schema["paths"]) >= {"/api/projects", "/api/projects/{project_id}"}


def test_cors_allows_only_configured_local_origin(tmp_path):
    app = create_app(make_registry(tmp_path))
    allowed = request(
        app,
        "OPTIONS",
        "/api/projects",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"

    post_allowed = request(
        app,
        "OPTIONS",
        "/api/projects/lobby/annotations",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert post_allowed.status_code == 200
    assert "POST" in post_allowed.headers["access-control-allow-methods"]

    denied = request(
        app,
        "GET",
        "/api/projects",
        headers={"Origin": "https://example.invalid"},
    )
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_cors_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="must be explicit"):
        create_app(make_registry(tmp_path), cors_origins=["*"])


def test_project_endpoints_take_type_and_status_from_task_adapter(tmp_path):
    class DemoTask:
        type_name = "demo"

        def load(self, config_path: Path) -> TaskProject:
            return TaskProject(config_path, tmp_path / "demo-data", object())

        def queue(self, project: TaskProject, request: QueueRequest) -> QueuePage:
            return QueuePage(0, request.offset, request.limit, ())

        def submit(
            self, project: TaskProject, submission: Submission
        ) -> SubmissionResult:
            raise NotImplementedError

        def status(self, project: TaskProject) -> TaskStatus:
            return TaskStatus("ready", {"items": 7})

        def export(
            self, project: TaskProject, request: ExportRequest
        ) -> ExportResult:
            return ExportResult(request.format, ())

    config = tmp_path / "demo.yaml"
    config.write_text("demo: true\n", encoding="utf-8")
    registry = tmp_path / "projects.yaml"
    registry.write_text(
        "projects:\n"
        "  - id: custom\n"
        "    name: Custom\n"
        "    task_type: demo\n"
        "    config: ./demo.yaml\n",
        encoding="utf-8",
    )
    app = create_app(
        registry,
        task_types=TaskTypeRegistry((DemoTask(),)),
    )

    listing = request(app, "GET", "/api/projects").json()
    assert listing[0]["task_type"] == "demo"
    assert listing[0]["status"] == "ready"
    detail = request(app, "GET", "/api/projects/custom").json()
    assert detail["summary"] == {"items": 7}


def test_review_queue_submit_refresh_and_duplicate_safety(tmp_path):
    registry, review = make_review_registry(tmp_path)
    app = create_app(registry)

    queue = request(app, "GET", "/api/projects/lobby/queue?status=pending&limit=1")
    assert queue.status_code == 200
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["gallery1"] == ["images/train/a/00.jpg"]

    payload = {"item_id": "c1", "result": {"label": "same", "notes": "clear"}}
    saved = request(app, "POST", "/api/projects/lobby/annotations", json=payload)
    assert saved.status_code == 200
    assert saved.json()["status"]["state"] == "reviewed"
    assert read_csv(review)[0]["review_label"] == "same"

    # A retry after an ambiguous network response is idempotent.
    assert request(
        app, "POST", "/api/projects/lobby/annotations", json=payload
    ).status_code == 200
    conflict = request(
        app,
        "POST",
        "/api/projects/lobby/annotations",
        json={"item_id": "c1", "result": {"label": "different"}},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "task_conflict"
    assert read_csv(review)[0]["review_label"] == "same"

    refreshed = request(app, "GET", "/api/projects/lobby/queue?status=pending")
    assert refreshed.json()["total"] == 0


def test_review_rejects_bad_submission_without_changing_csv(tmp_path):
    registry, review = make_review_registry(tmp_path)
    response = request(
        create_app(registry),
        "POST",
        "/api/projects/lobby/annotations",
        json={"item_id": "c1", "result": {"label": "maybe"}},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "task_operation_error"
    assert read_csv(review)[0]["review_label"] == ""


def test_project_media_is_read_only_and_confined_to_dataset(tmp_path):
    registry, _ = make_review_registry(tmp_path)
    app = create_app(registry)
    image = request(app, "GET", "/api/projects/lobby/files/images/train/a/00.jpg")
    assert image.status_code == 200
    assert image.content == b"jpeg"

    escaped = request(app, "GET", "/api/projects/lobby/files/../reid.yaml")
    assert escaped.status_code == 404

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"private")
    (tmp_path / "dataset" / "escape.jpg").symlink_to(outside)
    symlink = request(app, "GET", "/api/projects/lobby/files/escape.jpg")
    assert symlink.status_code == 404


def test_write_failure_is_reported_and_preserves_existing_decision(
    tmp_path, monkeypatch
):
    registry, review = make_review_registry(tmp_path)

    def fail_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("annotation_platform.reid_task.Store.set_label", fail_write)
    response = request(
        create_app(registry),
        "POST",
        "/api/projects/lobby/annotations",
        json={"item_id": "c1", "result": {"label": "same"}},
    )
    assert response.status_code == 422
    assert "disk full" in response.json()["detail"]["message"]
    assert read_csv(review)[0]["review_label"] == ""
