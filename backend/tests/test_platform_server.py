import asyncio
from pathlib import Path

import httpx
import pytest

from annotation_platform.server import create_app


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
