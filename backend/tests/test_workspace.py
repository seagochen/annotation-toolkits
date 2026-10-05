import base64
import io
import zipfile
from pathlib import Path

import pytest
import yaml

from annotation_platform.server import create_app
from test_platform_server import make_classification_registry, request

JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body"
PNG = b"\x89PNG\r\n\x1a\n" + b"png-body"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"webp-body"

IMAGE_TYPES = {
    "classification": {"labels": ["cat", "dog"]},
    "captioning": {},
    "detection": {"categories": ["person", "car"]},
    "segmentation": {"categories": ["road", "building"]},
    "depth": {},
}


def make_app(tmp_path: Path, *roots: Path):
    return create_app(tmp_path / "ws", cors_origins=[], import_roots=list(roots))


def create(app, name: str, task_type: str, settings: dict | None = None):
    return request(
        app,
        "POST",
        "/api/projects",
        json={"name": name, "task_type": task_type, "settings": settings or {}},
    )


def upload(app, project_id: str, path: str, content: bytes = JPEG):
    return request(
        app,
        "POST",
        f"/api/projects/{project_id}/import/files",
        params={"path": path},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def first_item(app, project_id: str) -> str:
    return request(app, "GET", f"/api/projects/{project_id}/queue").json()["items"][0]["item_id"]


def registry_entries(tmp_path: Path) -> list[dict]:
    return yaml.safe_load((tmp_path / "ws" / "projects.yaml").read_text())["projects"]


def test_empty_workspace_lists_nothing_and_creates_nothing(tmp_path):
    app = make_app(tmp_path)
    response = request(app, "GET", "/api/projects")
    assert response.status_code == 200
    assert response.json() == []
    assert not (tmp_path / "ws").exists()


def test_task_types_describe_forms_in_ui_order(tmp_path):
    types = request(make_app(tmp_path), "GET", "/api/task-types").json()
    assert [item["type"] for item in types] == [
        "classification", "captioning", "detection", "segmentation", "depth", "reid",
    ]
    detection = types[2]
    assert detection["import_modes"] == ["upload", "directory"]
    assert [item["format"] for item in detection["export_formats"]] == ["native", "coco"]
    categories = detection["fields"][0]
    assert categories["key"] == "categories"
    assert categories["lock"] == "append_only"
    assert categories["required"] is True
    reid = types[-1]
    assert reid["import_modes"] == ["directory", "managed"]
    server_only = {field["key"] for field in reid["fields"] if field["server_only"]}
    assert server_only == {"train.trainer", "train.python", "evaluate.evaluator", "evaluate.python"}


@pytest.mark.parametrize("task_type", [*IMAGE_TYPES, "reid"])
def test_create_each_task_type_as_managed_project(tmp_path, task_type):
    app = make_app(tmp_path)
    response = create(app, f"Demo {task_type}", task_type, IMAGE_TYPES.get(task_type, {}))
    assert response.status_code == 201, response.text
    detail = response.json()
    project_id = f"demo-{task_type}"
    project_dir = tmp_path / "ws" / "projects" / project_id
    assert detail["id"] == project_id
    assert detail["task_type"] == task_type
    assert detail["root"] == str(project_dir / "data")
    assert (project_dir / "data").is_dir()
    config = yaml.safe_load((project_dir / "config.yaml").read_text())
    assert config["dataset"] == "./data"
    assert registry_entries(tmp_path) == [
        {
            "id": project_id,
            "name": f"Demo {task_type}",
            "task_type": task_type,
            "config": f"projects/{project_id}/config.yaml",
        }
    ]
    assert request(app, "GET", "/api/projects").json()[0]["id"] == project_id

    settings = request(app, "GET", f"/api/projects/{project_id}/settings").json()
    assert settings["data_source"] == {"mode": "managed", "path": str(project_dir / "data")}
    assert settings["annotated"] is False
    if task_type == "reid":
        assert config["pipeline"] == {"script": "ultralytics", "device": "cuda:0"}
        assert settings["values"]["mine.min_cosine"] == 0.8


def test_project_ids_are_slugged_and_unique(tmp_path):
    app = make_app(tmp_path)
    ids = [
        create(app, name, "captioning").json()["id"]
        for name in ("My Project!", "my project", "猫咪", "猫狗", "  Café Été  ")
    ]
    # Names with no ASCII letters fall back to the task type.
    assert ids == ["my-project", "my-project-2", "captioning", "captioning-2", "cafe-ete"]
    assert request(app, "GET", "/api/projects/cafe-ete").json()["name"] == "Café Été"


def test_create_rejects_bad_input_and_keeps_registry_valid(tmp_path):
    app = make_app(tmp_path)
    assert create(app, "Keep", "captioning").status_code == 201

    cases = [
        ({"name": "  ", "task_type": "captioning"}, "invalid_name"),
        ({"name": "X", "task_type": "nope"}, "unknown_task_type"),
        ({"name": "X", "task_type": "detection"}, "missing_setting"),
        ({"name": "X", "task_type": "captioning", "settings": {"bogus": 1}}, "unknown_setting"),
        ({"name": "X", "task_type": "detection", "settings": {"categories": "car"}}, "invalid_setting"),
        ({"name": "X", "task_type": "reid", "settings": {"train.python": "/bin/sh"}}, "server_only_field"),
        # Passes the form checks, rejected by the module itself.
        (
            {"name": "X", "task_type": "segmentation", "settings": {"categories": [f"c{i}" for i in range(255)]}},
            "invalid_config",
        ),
        ({"name": "X", "task_type": "classification", "settings": {"labels": ["a", "a"]}}, "invalid_config"),
    ]
    for body, code in cases:
        response = request(app, "POST", "/api/projects", json=body)
        assert response.status_code == 422, (body, response.text)
        assert response.json()["detail"]["code"] == code

    assert [item["id"] for item in request(app, "GET", "/api/projects").json()] == ["keep"]
    assert sorted(path.name for path in (tmp_path / "ws" / "projects").iterdir()) == ["keep"]


def test_settings_edit_and_locks_after_annotation(tmp_path):
    app = make_app(tmp_path)
    create(app, "Pets", "classification", {"labels": ["cat", "dog"]})
    settings = request(app, "GET", "/api/projects/pets/settings").json()
    assert settings["values"] == {
        "labels": ["cat", "dog"],
        "mode": "single",
        "patterns": ["**/*.jpg", "**/*.jpeg", "**/*.png", "**/*.webp"],
    }
    assert settings["import_roots"] == [str(tmp_path / "ws")]
    assert "labels:" in settings["config_text"]

    # Before any annotation everything is editable, including order and mode.
    response = request(
        app,
        "PUT",
        "/api/projects/pets/settings",
        json={"name": "Pets v2", "settings": {"labels": ["dog", "cat"], "mode": "multi"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Pets v2"
    assert response.json()["values"]["labels"] == ["dog", "cat"]
    assert registry_entries(tmp_path)[0]["name"] == "Pets v2"

    assert upload(app, "pets", "a.jpg").status_code == 200
    submitted = request(
        app,
        "POST",
        "/api/projects/pets/annotations",
        json={"item_id": first_item(app, "pets"), "result": {"labels": ["dog"]}},
    )
    assert submitted.status_code == 200
    assert request(app, "GET", "/api/projects/pets/settings").json()["annotated"] is True

    for change in ({"labels": ["cat", "dog"]}, {"labels": ["dog"]}, {"mode": "single"}):
        response = request(app, "PUT", "/api/projects/pets/settings", json={"settings": change})
        assert response.status_code == 409, change
        assert response.json()["detail"]["code"] == "setting_locked"

    appended = request(
        app,
        "PUT",
        "/api/projects/pets/settings",
        json={"settings": {"labels": ["dog", "cat", "bird"], "mode": "multi"}},
    )
    assert appended.status_code == 200
    assert appended.json()["values"]["labels"] == ["dog", "cat", "bird"]

    missing = request(app, "PUT", "/api/projects/missing/settings", json={"name": "x"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "project_not_found"


def test_server_only_fields_and_pipeline_script_are_protected(tmp_path):
    app = make_app(tmp_path)
    create(app, "Lobby", "reid", {"pipeline.device": "cpu", "extract.frame_stride": 2})
    config_path = tmp_path / "ws" / "projects" / "lobby" / "config.yaml"
    # An administrator configures the trainer on the server itself.
    document = yaml.safe_load(config_path.read_text())
    document["train"]["trainer"] = "/opt/trainer"
    config_path.write_text(yaml.safe_dump(document))

    settings = request(app, "GET", "/api/projects/lobby/settings").json()
    values = settings["values"]
    assert values["train.trainer"] == "/opt/trainer"
    assert values["pipeline.device"] == "cpu"
    assert values["extract.frame_stride"] == 2

    # Sending the whole form back unchanged is fine.
    unchanged = request(app, "PUT", "/api/projects/lobby/settings", json={"settings": values})
    assert unchanged.status_code == 200, unchanged.text

    for change in ({"train.trainer": "/tmp/evil"}, {"train.trainer": None}, {"evaluate.python": "/bin/sh"}):
        response = request(app, "PUT", "/api/projects/lobby/settings", json={"settings": change})
        assert response.status_code == 422, change
        assert response.json()["detail"]["code"] == "server_only_field"
    rejected = request(
        app, "PUT", "/api/projects/lobby/settings", json={"settings": {"pipeline.script": "/tmp/x.py"}}
    )
    assert rejected.json()["detail"]["code"] == "invalid_setting"

    text = config_path.read_text()
    raw_cases = [
        text.replace("/opt/trainer", "/tmp/evil"),
        text.replace("script: ultralytics", "script: /tmp/evil.py"),
    ]
    for candidate in raw_cases:
        response = request(app, "PUT", "/api/projects/lobby/config", json={"text": candidate})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "server_only_field"
    assert config_path.read_text() == text

    bundled = request(
        app,
        "PUT",
        "/api/projects/lobby/config",
        json={"text": text.replace("script: ultralytics", "script: tracking_csv")},
    )
    assert bundled.status_code == 200
    assert bundled.json()["values"]["pipeline.script"] == "tracking_csv"


def test_raw_config_cannot_redirect_writes_or_trainer_configs(tmp_path):
    app = make_app(tmp_path)
    create(app, "Lobby", "reid")
    config_path = tmp_path / "ws" / "projects" / "lobby" / "config.yaml"
    original = config_path.read_text()
    for section, key, value in (
        ("extract", "out", "/etc"),
        ("mine", "output_dir", "/tmp/elsewhere"),
        ("train", "set", ["model.pretrained_path=/tmp/x.pt"]),
        ("train", "base_config", "/tmp/trainer.yaml"),
        ("evaluate", "set", ["checkpoint.path=/tmp/x.pt"]),
        ("evaluate", "base_config", "/tmp/evaluator.yaml"),
    ):
        document = yaml.safe_load(original)
        document.setdefault(section, {})[key] = value
        response = request(
            app, "PUT", "/api/projects/lobby/config", json={"text": yaml.safe_dump(document)}
        )
        assert response.status_code == 422, (section, key)
        assert response.json()["detail"]["code"] == "server_only_field"
    assert config_path.read_text() == original

    # An administrator's existing value is kept, and explicit empty defaults
    # (`set: []`) count as unchanged.
    document = yaml.safe_load(original)
    document["train"]["set"] = []
    document["evaluate"] = {"set": [], "base_config": ""}
    assert (
        request(app, "PUT", "/api/projects/lobby/config", json={"text": yaml.safe_dump(document)})
        .status_code
        == 200
    )

    # Not form fields either: create and the form reject them as unknown.
    assert create(app, "X", "reid", {"extract.out": "/etc"}).json()["detail"]["code"] == "unknown_setting"
    response = request(
        app, "PUT", "/api/projects/lobby/settings", json={"settings": {"train.set": ["a=b"]}}
    )
    assert response.json()["detail"]["code"] == "unknown_setting"


def test_model_paths_must_not_point_into_the_project_tree(tmp_path):
    allowed = tmp_path / "models"
    allowed.mkdir()
    app = make_app(tmp_path)
    create(app, "Other", "captioning")
    planted = tmp_path / "ws" / "projects" / "other" / "data" / "weights.jpg"

    for value in (str(planted), "./data/weights.pt", "../other/data/weights.jpg", str(tmp_path / "ws" / "projects")):
        response = create(app, "Lobby", "reid", {"pipeline.detector": value})
        assert response.status_code == 422, value
        assert response.json()["detail"]["code"] == "path_not_allowed"
    lobby = create(app, "Lobby", "reid", {"pipeline.detector": str(allowed / "yolo.pt")})
    assert lobby.status_code == 201

    for key in ("pipeline.reid_onnx", "mine.reid_onnx", "evaluate.checkpoint"):
        response = request(
            app, "PUT", "/api/projects/lobby/settings", json={"settings": {key: str(planted)}}
        )
        assert response.status_code == 422, key
        assert response.json()["detail"]["code"] == "path_not_allowed"
    # A symlink outside the tree that resolves into it is caught as well.
    link = allowed / "innocent.pt"
    link.symlink_to(planted)
    response = request(
        app, "PUT", "/api/projects/lobby/settings", json={"settings": {"mine.reid_onnx": str(link)}}
    )
    assert response.json()["detail"]["code"] == "path_not_allowed"
    ok = request(
        app,
        "PUT",
        "/api/projects/lobby/settings",
        json={"settings": {"mine.reid_onnx": str(allowed / "reid.onnx")}},
    )
    assert ok.status_code == 200

    config_path = tmp_path / "ws" / "projects" / "lobby" / "config.yaml"
    document = yaml.safe_load(config_path.read_text())
    document["models"] = {"head": {"path": "./data/head.pt"}}
    response = request(
        app, "PUT", "/api/projects/lobby/config", json={"text": yaml.safe_dump(document)}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "path_not_allowed"
    document["models"] = {"head": {"path": str(allowed / "head.pt")}}
    response = request(
        app, "PUT", "/api/projects/lobby/config", json={"text": yaml.safe_dump(document)}
    )
    assert response.status_code == 200, response.text


def test_raw_config_editor_keeps_text_and_protects_paths(tmp_path):
    app = make_app(tmp_path)
    create(app, "Cars", "detection", {"categories": ["car"]})
    config_path = tmp_path / "ws" / "projects" / "cars" / "config.yaml"
    original = config_path.read_text()

    cases = [
        (original.replace("./data", "/etc"), "protected_setting"),
        (original + "annotations: elsewhere.json\n", "protected_setting"),
        ("dataset: ./data\ncategories: [car\n", "invalid_config"),
        ("- a\n- b\n", "invalid_config"),
        (original + "colour: red\n", "invalid_config"),
    ]
    for text, code in cases:
        response = request(app, "PUT", "/api/projects/cars/config", json={"text": text})
        assert response.status_code == 422, text
        assert response.json()["detail"]["code"] == code
    assert config_path.read_text() == original
    assert not list(config_path.parent.glob("*.tmp"))

    edited = "# hand-tuned\n" + original.replace("- car", "- car\n- truck")
    response = request(app, "PUT", "/api/projects/cars/config", json={"text": edited})
    assert response.status_code == 200, response.text
    assert response.json()["values"]["categories"] == ["car", "truck"]
    assert config_path.read_text() == edited

    # Once annotated, the raw editor is held to the same lock rules as the form.
    upload(app, "cars", "a.jpg")
    request(
        app,
        "POST",
        "/api/projects/cars/annotations",
        json={
            "item_id": first_item(app, "cars"),
            "result": {"image_size": {"width": 4, "height": 4}, "boxes": []},
        },
    )
    reordered = edited.replace("- car\n- truck", "- truck\n- car")
    response = request(app, "PUT", "/api/projects/cars/config", json={"text": reordered})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "setting_locked"


def test_upload_accepts_images_only_inside_the_managed_dataset(tmp_path):
    app = make_app(tmp_path)
    create(app, "Shots", "captioning")
    data = tmp_path / "ws" / "projects" / "shots" / "data"

    response = upload(app, "shots", "day 1/a.jpg", JPEG)
    assert response.status_code == 200
    assert response.json() == {"path": "day 1/a.jpg", "size": len(JPEG)}
    assert (data / "day 1" / "a.jpg").read_bytes() == JPEG
    assert upload(app, "shots", "day 1/a.jpg", PNG).status_code == 200
    assert (data / "day 1" / "a.jpg").read_bytes() == PNG
    assert request(app, "GET", "/api/projects/shots/queue").json()["total"] == 1

    # Extensions are stored lowercase so the default patterns find them; the
    # normalized name replaces an existing file like any same-path upload.
    uppercase = upload(app, "shots", "day 1/IMG_0001.JPG", JPEG)
    assert uppercase.json() == {"path": "day 1/IMG_0001.jpg", "size": len(JPEG)}
    assert upload(app, "shots", "day 1/IMG_0001.Jpg", PNG).status_code == 200
    assert (data / "day 1" / "IMG_0001.jpg").read_bytes() == PNG
    assert upload(app, "shots", "B.WebP", WEBP).json()["path"] == "B.webp"
    assert request(app, "GET", "/api/projects/shots/queue").json()["total"] == 3

    # The signature decides, not the extension: any of the three formats is
    # fine under any image extension, anything else is refused.
    assert upload(app, "shots", "png-named.jpg", PNG).status_code == 200
    for content in (b"image", b"\x80\x04\x95pickle-payload", b"RIFF\x00\x00\x00\x00WAVE", b"\xff\xd8"):
        response = upload(app, "shots", "fake.jpg", content)
        assert response.status_code == 422, content
        assert response.json()["detail"]["code"] == "unsupported_file"
    assert not (data / "fake.jpg").exists()
    assert not list(data.glob(".upload-*"))

    cases = [
        (".annotations/caption.json.png", "invalid_path"),
        ("a/.hidden/x.png", "invalid_path"),
        ("../escape.jpg", "invalid_path"),
        ("a/../../escape.jpg", "invalid_path"),
        ("/abs.jpg", "invalid_path"),
        ("a//b.jpg", "invalid_path"),
        ("notes.txt", "unsupported_file"),
        ("archive.zip", "unsupported_file"),
    ]
    for path, code in cases:
        response = upload(app, "shots", path)
        assert response.status_code == 422, path
        assert response.json()["detail"]["code"] == code
    assert upload(app, "shots", "empty.png", b"").json()["detail"]["code"] == "empty_file"
    assert not (tmp_path / "ws" / "projects" / "shots" / "escape.jpg").exists()
    assert sorted(path.name for path in data.rglob("*")) == [
        "B.webp", "IMG_0001.jpg", "a.jpg", "day 1", "png-named.jpg",
    ]
    assert upload(app, "missing", "a.jpg").status_code == 404

    create(app, "Lobby", "reid")
    response = upload(app, "lobby", "a.jpg")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "upload_not_supported"


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("photos/", b"")
        for name, content in entries.items():
            bundle.writestr(name, content)
    return buffer.getvalue()


def test_archive_import_extracts_images_and_skips_everything_else(tmp_path):
    app = make_app(tmp_path)
    create(app, "Boxes", "detection", {"categories": ["box"]})
    data = tmp_path / "ws" / "projects" / "boxes" / "data"
    archive = zip_bytes(
        {
            "photos/a.jpg": JPEG,
            "photos/sub/B.PNG": PNG,
            "photos/c.WEBP": WEBP,
            "photos/fake.jpg": b"\x80\x04\x95pickle-payload",
            "../evil.jpg": JPEG,
            "/abs.jpg": JPEG,
            "__MACOSX/photos/._a.jpg": JPEG,
            "photos/.annotations/detection.json.jpg": JPEG,
            "photos/readme.txt": b"text",
        }
    )
    response = request(
        app,
        "POST",
        "/api/projects/boxes/import/archive",
        content=archive,
        headers={"Content-Type": "application/zip"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"imported": 3, "skipped": 6}
    assert (data / "photos" / "a.jpg").read_bytes() == JPEG
    assert (data / "photos" / "sub" / "B.png").read_bytes() == PNG
    assert (data / "photos" / "c.webp").read_bytes() == WEBP
    assert not (data / "photos" / "fake.jpg").exists()
    assert not (data.parent / "evil.jpg").exists()
    assert not (tmp_path / "ws" / "projects" / "evil.jpg").exists()
    assert not list(data.parent.glob(".upload-*"))

    broken = request(app, "POST", "/api/projects/boxes/import/archive", content=b"not a zip")
    assert broken.status_code == 422
    assert broken.json()["detail"]["code"] == "invalid_archive"


def test_text_tasks_accept_utf8_documents_and_nothing_else(tmp_path):
    app = make_app(tmp_path)
    create(app, "Reviews", "classification", {
        "labels": ["positive", "negative"], "patterns": ["**/*.txt", "**/*.md"],
    })
    data = tmp_path / "ws" / "projects" / "reviews" / "data"
    types = {item["type"]: item for item in request(app, "GET", "/api/task-types").json()}
    assert types["classification"]["upload_extensions"] == [
        ".jpeg", ".jpg", ".md", ".png", ".txt", ".webp",
    ]
    assert types["detection"]["upload_extensions"] == [".jpeg", ".jpg", ".png", ".webp"]
    assert types["reid"]["upload_extensions"] == []

    text = "很好用。\n第二行".encode("utf-8")
    response = upload(app, "reviews", "batch 1/a.TXT", text)
    assert response.status_code == 200, response.text
    assert response.json() == {"path": "batch 1/a.txt", "size": len(text)}
    assert upload(app, "reviews", "b.md", "# 标题".encode("utf-8")).status_code == 200
    for path, content in (("latin.txt", b"caf\xe9"), ("nul.txt", b"a\x00b"), ("x.json", b"{}")):
        response = upload(app, "reviews", path, content)
        assert response.status_code == 422, path
        assert response.json()["detail"]["code"] == "unsupported_file"
    queue = request(app, "GET", "/api/projects/reviews/queue").json()
    assert [(item["image_path"], item["text"]) for item in queue["items"]] == [
        ("b.md", "# 标题"), ("batch 1/a.txt", "很好用。\n第二行"),
    ]

    archive = zip_bytes({
        "docs/c.txt": "第三篇".encode("utf-8"),
        "docs/bad.txt": b"\xff\xfe",
        "docs/d.jpg": JPEG,
        "docs/e.csv": b"a,b",
    })
    response = request(
        app, "POST", "/api/projects/reviews/import/archive",
        content=archive, headers={"Content-Type": "application/zip"},
    )
    assert response.json() == {"imported": 2, "skipped": 2}
    assert (data / "docs" / "c.txt").read_text(encoding="utf-8") == "第三篇"
    assert not (data / "docs" / "bad.txt").exists()
    assert not list(data.rglob(".upload-*"))

    # Detection projects still take images only.
    create(app, "Boxes", "detection", {"categories": ["box"]})
    assert upload(app, "boxes", "a.txt", text).json()["detail"]["code"] == "unsupported_file"


def test_link_directory_is_confined_to_allowed_roots(tmp_path):
    allowed = tmp_path / "allowed"
    (allowed / "set-a").mkdir(parents=True)
    (allowed / "set-a" / "x.jpg").write_bytes(b"x")
    (allowed / "set-b").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    app = make_app(tmp_path, allowed)
    create(app, "Linked", "classification", {"labels": ["a"]})

    for path, code in (
        (str(outside), "path_not_allowed"),
        (str(allowed / ".." / "outside"), "path_not_allowed"),
        ("/", "path_not_allowed"),
        (str(tmp_path / "ws" / "projects"), "path_not_allowed"),
        (str(tmp_path / "ws"), "path_not_allowed"),
        ("relative/dir", "invalid_directory"),
        (str(allowed / "missing"), "invalid_directory"),
    ):
        response = request(app, "POST", "/api/projects/linked/import/directory", json={"path": path})
        assert response.status_code == 422, path
        assert response.json()["detail"]["code"] == code
    (outside / "trap").symlink_to(outside)
    escaped = request(
        app, "POST", "/api/projects/linked/import/directory", json={"path": str(outside / "trap")}
    )
    assert escaped.json()["detail"]["code"] == "path_not_allowed"

    response = request(
        app, "POST", "/api/projects/linked/import/directory", json={"path": str(allowed / "set-a")}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data_source"] == {"mode": "directory", "path": str(allowed / "set-a")}
    assert body["import_roots"] == [str(tmp_path / "ws"), str(allowed)]
    assert request(app, "GET", "/api/projects/linked/queue").json()["total"] == 1

    not_managed = upload(app, "linked", "a.jpg")
    assert not_managed.status_code == 409
    assert not_managed.json()["detail"]["code"] == "not_managed"

    # Re-linking a directory-mode project is allowed, sharing a dataset is not.
    relinked = request(
        app, "POST", "/api/projects/linked/import/directory", json={"path": str(allowed / "set-b")}
    )
    assert relinked.status_code == 200
    create(app, "Other", "captioning")
    clash = request(
        app, "POST", "/api/projects/other/import/directory", json={"path": str(allowed / "set-b")}
    )
    assert clash.status_code == 409
    assert clash.json()["detail"]["code"] == "dataset_in_use"

    # A managed project that already holds files cannot be switched over.
    upload(app, "other", "a.jpg")
    present = request(
        app, "POST", "/api/projects/other/import/directory", json={"path": str(allowed / "set-a")}
    )
    assert present.status_code == 409
    assert present.json()["detail"]["code"] == "managed_data_present"

    # Inside the workspace (outside its project tree) is always allowed.
    inside = tmp_path / "ws" / "shared" / "set"
    inside.mkdir(parents=True)
    create(app, "Inside", "captioning")
    assert (
        request(
            app, "POST", "/api/projects/inside/import/directory", json={"path": str(inside)}
        ).status_code
        == 200
    )


def test_reid_can_link_an_extracted_dataset(tmp_path):
    dataset = tmp_path / "allowed" / "scene"
    dataset.mkdir(parents=True)
    (dataset / "identities.csv").write_text("identity_id\nperson-1\n", encoding="utf-8")
    app = make_app(tmp_path, tmp_path / "allowed")
    create(app, "Scene", "reid")
    response = request(
        app, "POST", "/api/projects/scene/import/directory", json={"path": str(dataset)}
    )
    assert response.status_code == 200, response.text
    detail = request(app, "GET", "/api/projects/scene").json()
    assert detail["root"] == str(dataset)
    assert detail["summary"]["identities"] == 1


def test_delete_removes_only_platform_owned_files(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "keep.jpg").write_bytes(b"x")
    app = make_app(tmp_path, allowed)
    create(app, "Managed", "captioning")
    upload(app, "managed", "a.jpg")
    create(app, "Linked", "captioning")
    request(app, "POST", "/api/projects/linked/import/directory", json={"path": str(allowed)})

    for project_id in ("managed", "linked"):
        response = request(app, "DELETE", f"/api/projects/{project_id}")
        assert response.status_code == 204
        assert not (tmp_path / "ws" / "projects" / project_id).exists()
        assert request(app, "GET", f"/api/projects/{project_id}").status_code == 404
    assert (allowed / "keep.jpg").is_file()
    assert registry_entries(tmp_path) == []
    assert request(app, "GET", "/api/projects").json() == []
    assert request(app, "DELETE", "/api/projects/managed").status_code == 404

    # The id is free again once the directory is gone.
    assert create(app, "Managed", "captioning").json()["id"] == "managed"


def test_delete_of_hand_registered_project_only_unregisters_it(tmp_path):
    registry = make_classification_registry(tmp_path)
    app = create_app(registry, cors_origins=[])
    assert request(app, "DELETE", "/api/projects/scenes").status_code == 204
    assert request(app, "GET", "/api/projects").json() == []
    assert (tmp_path / "classification.yaml").is_file()
    assert (tmp_path / "classification-images" / "sample.jpg").is_file()


def test_export_downloads_single_files_and_zips_multi_file_exports(tmp_path):
    app = make_app(tmp_path)
    create(app, "Cars", "detection", {"categories": ["car"]})
    upload(app, "cars", "a.jpg")
    request(
        app,
        "POST",
        "/api/projects/cars/annotations",
        json={
            "item_id": first_item(app, "cars"),
            "result": {
                "image_size": {"width": 4, "height": 4},
                "boxes": [{"category": "car", "x": 0, "y": 0, "width": 2, "height": 2}],
            },
        },
    )
    native = request(app, "GET", "/api/projects/cars/export")
    assert native.status_code == 200
    assert native.headers["content-disposition"] == 'attachment; filename="cars-native.json"'
    assert len(native.json()["items"]) == 1
    coco = request(app, "GET", "/api/projects/cars/export", params={"format": "coco"})
    assert coco.headers["content-disposition"] == 'attachment; filename="cars-coco.json"'
    assert coco.json()["categories"] == [{"id": 1, "name": "car"}]
    unknown = request(app, "GET", "/api/projects/cars/export", params={"format": "voc"})
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "export_failed"

    create(app, "Roads", "segmentation", {"categories": ["road"]})
    upload(app, "roads", "a.png")
    request(
        app,
        "POST",
        "/api/projects/roads/annotations",
        json={
            "item_id": first_item(app, "roads"),
            "result": {
                "image_size": {"width": 1, "height": 1},
                "pixels": base64.b64encode(b"\x01").decode(),
            },
        },
    )
    masks = request(app, "GET", "/api/projects/roads/export")
    assert masks.status_code == 200
    assert masks.headers["content-disposition"] == 'attachment; filename="roads-native.zip"'
    names = zipfile.ZipFile(io.BytesIO(masks.content)).namelist()
    assert ".annotations/segmentation/index.json" in names
    assert len(names) == 2

    create(app, "Lobby", "reid")
    nothing = request(app, "GET", "/api/projects/lobby/export")
    assert nothing.status_code == 422
    assert nothing.json()["detail"]["code"] == "export_failed"
    assert request(app, "GET", "/api/projects/missing/export").status_code == 404


def test_cors_allows_management_methods(tmp_path):
    app = create_app(tmp_path / "ws", cors_origins=["http://localhost:5173"])
    for method in ("PUT", "DELETE"):
        response = request(
            app,
            "OPTIONS",
            "/api/projects/x",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
            },
        )
        assert response.status_code == 200
        assert method in response.headers["access-control-allow-methods"]


def test_relative_import_roots_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        create_app(tmp_path / "ws", import_roots=["relative"])
