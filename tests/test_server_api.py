"""End-to-end HTTP tests for the config/model/job control-plane routes added
in Phase C. These spin up a real ThreadingHTTPServer (the existing test suite
otherwise only exercises Store directly), because the new logic lives mostly
in routing and JSON marshalling on ReviewHandler itself.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from reid_annotation_tool.jobs import JobRunner
from reid_annotation_tool.server import ReviewHandler, Store


@pytest.fixture
def live_server(dataset):
    config_path = dataset / "reid.yaml"
    config_path.write_text("dataset: .\n", encoding="utf-8")
    # No live round yet -- a brand-new project's review tab has nothing to
    # show, but the server must still start (see stage_serve's no-hard-exit).
    # This mirrors app.py's real call: `candidates=None` when live_round() is
    # None, never a path to a review round that doesn't exist yet.
    ReviewHandler.store = Store(dataset, None, dataset / "pairs.csv", [])
    ReviewHandler.config_path = config_path
    ReviewHandler.jobs = JobRunner(dataset / ".jobs")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ReviewHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def request(httpd, method, path, body=None):
    """A non-2xx response is send_error()'s stock HTML page, not JSON --
    callers checking error paths only care about the status code."""
    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
    headers = {"Content-Type": "application/json"} if body is not None else {}
    payload = json.dumps(body).encode() if body is not None else None
    conn.request(method, path, body=payload, headers=headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    try:
        return response.status, (json.loads(data) if data else None)
    except json.JSONDecodeError:
        return response.status, None


def test_state_on_a_brand_new_project_is_empty_not_an_error(live_server):
    status, body = request(live_server, "GET", "/api/state")
    assert status == 200
    assert body["total"] == 0 and body["pending"] == 0


def test_config_get_and_schema(live_server):
    status, body = request(live_server, "GET", "/api/config")
    assert status == 200
    assert body["sections"]["crops"]["min_blur"] == 60.0
    assert "pipeline" in body["open_sections"]

    status, schema = request(live_server, "GET", "/api/config/schema")
    assert status == 200
    assert schema["crops"]["min_blur"] == {"default": 60.0, "type": "float"}


def test_saving_a_config_section_persists_and_validates(live_server):
    status, body = request(live_server, "POST", "/api/config/crops", {"min_blur": 42.0})
    assert status == 200 and body["min_blur"] == 42.0

    status, body = request(live_server, "GET", "/api/config")
    assert body["sections"]["crops"]["min_blur"] == 42.0
    # Untouched keys in the same section survive the merge.
    assert body["sections"]["crops"]["min_crop_side"] == 48


def test_saving_an_unknown_key_is_rejected_and_does_not_touch_the_file(live_server):
    original = ReviewHandler.config_path.read_text(encoding="utf-8")
    status, body = request(live_server, "POST", "/api/config/crops", {"not_a_real_key": 1})
    assert status == 400
    assert ReviewHandler.config_path.read_text(encoding="utf-8") == original


def test_models_crud(live_server):
    status, body = request(live_server, "GET", "/api/models")
    assert status == 200 and body == {}

    status, body = request(live_server, "POST", "/api/models/detector", {"path": "./w.pt"})
    assert status == 200 and body["framework"] == "pytorch"
    assert body["path"].endswith("/w.pt")

    status, body = request(live_server, "GET", "/api/models")
    assert status == 200 and "detector" in body

    status, body = request(live_server, "DELETE", "/api/models/detector")
    assert status == 200

    status, body = request(live_server, "GET", "/api/models")
    assert status == 200 and body == {}


def test_deleting_an_unknown_model_is_reported(live_server):
    status, body = request(live_server, "DELETE", "/api/models/nope")
    assert status == 400


def test_jobs_list_start_and_poll(live_server):
    status, body = request(live_server, "GET", "/api/jobs")
    assert status == 200 and body["jobs"] == []
    assert "check" in body["stages"] and "serve" not in body["stages"]

    status, body = request(live_server, "POST", "/api/jobs/check", {})
    assert status == 200
    job_id = body["id"]
    # `check` is fast enough on a near-empty dataset that it may already be
    # done by the time this response arrives -- only "failed" would be wrong.
    assert body["status"] != "failed"

    ReviewHandler.jobs.join(job_id, timeout=5)
    status, body = request(live_server, "GET", f"/api/jobs/{job_id}")
    assert status == 200 and body["status"] == "done"
    assert body["result"] == {"exit_code": 0}


def test_starting_an_unrunnable_stage_is_rejected(live_server):
    status, body = request(live_server, "POST", "/api/jobs/serve", {})
    assert status == 400


def test_unknown_job_id_is_a_404(live_server):
    status, body = request(live_server, "GET", "/api/jobs/does-not-exist")
    assert status == 404
