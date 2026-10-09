"""Hosted account boundaries are exercised against an HTTP identity transport."""
from datetime import datetime, timedelta, timezone
import hashlib
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi.testclient import TestClient
import pytest

from annotation_platform.hosted import create_hosted_app

ORIGIN = "https://annotation.apps.skillsmaster.jp"
TOKEN_A, TOKEN_B = "a" * 32, "b" * 32


@pytest.fixture
def hosted(tmp_path):
    state = {"revoked": set(), "unavailable": False, "appId": "annotation", "exchanges": []}
    def platform(request):
        if state["unavailable"]:
            return httpx.Response(503)
        token = request.headers.get("authorization", "")[7:]
        if request.url.path.endswith("/exchange"):
            state["exchanges"].append(request.read())
            return httpx.Response(200, json={"token": TOKEN_A, "expiresAt": expiry()})
        if token not in {TOKEN_A, TOKEN_B} or token in state["revoked"]:
            return httpx.Response(401)
        if request.method == "DELETE":
            state["revoked"].add(token)
            return httpx.Response(204)
        return httpx.Response(200, json={"appId": state["appId"], "userId": "alice" if token == TOKEN_A else "bob",
                                        "scopes": ["platform.auth"], "expiresAt": expiry()})
    transport = httpx.AsyncClient(transport=httpx.MockTransport(platform))
    app = create_hosted_app(tmp_path, ORIGIN, "https://www.skillsmaster.jp", client=transport)
    return app, state, tmp_path, transport


def expiry():
    return (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()


def auth(token=TOKEN_A):
    return {"Authorization": "Bearer " + token}


def test_crud_user_isolation_restart_and_unavailable(hosted):
    app, state, root, transport = hosted
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/projects").status_code == 401
        assert client.get("/healthz").status_code == 200
        created = client.post("/api/projects", headers=auth(), json={"name": "My labels", "task_type": "detection", "settings": {"categories": ["cat"]}})
        assert created.status_code == 201, created.text
        project = created.json()["id"]
        assert client.get("/api/projects", headers=auth(TOKEN_B)).json() == []
        assert client.get("/api/projects/" + project, headers=auth(TOKEN_B)).status_code == 404
        assert client.post(f"/api/projects/{project}/import/directory", headers=auth(), json={"path": "/data"}).status_code == 403
        assert client.post("/api/projects", headers=auth(), json={"name": "unsafe", "task_type": "reid"}).status_code == 422
        assert "reid" not in [row["type"] for row in client.get("/api/task-types", headers=auth()).json()]
        assert (root / "users" / hashlib.sha256(b"alice").hexdigest() / "projects.yaml").is_file()
        state["unavailable"] = True
        assert client.get("/api/projects", headers=auth()).status_code == 503
        state["unavailable"] = False
        state["appId"] = "image-studio"
        assert client.get("/api/projects", headers=auth()).status_code == 401
        state["appId"] = "annotation"
    restarted = create_hosted_app(root, ORIGIN, "https://www.skillsmaster.jp", client=transport)
    with TestClient(restarted, base_url=ORIGIN) as client:
        assert client.get("/api/projects", headers=auth()).json()[0]["id"] == project
        state["revoked"].add(TOKEN_A)
        assert client.get("/api/projects", headers=auth()).status_code == 401


def test_pkce_cookie_origin_and_logout(hosted):
    app, state, _, _ = hosted
    with TestClient(app, base_url=ORIGIN) as client:
        login = client.get("/auth/platform/login?next=/projects/a?view=queue", follow_redirects=False)
        params = parse_qs(urlsplit(login.headers["location"]).query)
        assert params["code_challenge_method"] == ["S256"]
        assert "Domain=" not in login.headers["set-cookie"]
        assert "Secure" in login.headers["set-cookie"]
        state_key = params["state"][0]
        url = "/auth/platform/callback?state=" + state_key + "&code=" + "c" * 32
        with TestClient(app, base_url=ORIGIN) as stranger:
            assert stranger.get(url).status_code == 401
        callback = client.get(url, follow_redirects=False)
        assert callback.status_code == 303
        assert callback.headers["location"] == "/projects/a?view=queue"
        assert client.get(url).status_code == 401
        assert client.get("/auth/platform/me").json()["userId"] == "alice"
        assert client.post("/api/projects", headers={"Origin": "https://image-studio.apps.skillsmaster.jp"}, json={"name": "Bad", "task_type": "detection"}).status_code == 403
        assert client.post("/auth/platform/logout").status_code == 403
        assert client.post("/auth/platform/logout", headers={"Origin": ORIGIN}).status_code == 204
        assert client.get("/api/projects").status_code == 401
        assert len(state["exchanges"]) == 1
