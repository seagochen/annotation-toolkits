"""Every export a user can download matches a versioned, documented contract."""

import base64
import io
import json
import re
import shlex
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from annotation_platform.export_contracts import (
    BY_ID,
    CONTRACTS,
    ContractViolation,
    validate_files,
)
from annotation_platform.exports import main as exports_main
from annotation_platform.project_forms import TASK_TYPE_SPECS
from annotation_platform.server import EXPORT_CONTRACT_HEADER, create_app
from test_platform_server import make_review_registry, request, wait_for_action
from test_workspace import JPEG, create, upload

BACKEND = Path(__file__).resolve().parents[1]
INTERFACE_DOC = BACKEND.parent / "docs" / "detailed_design" / "70_外部接口.md"

PIXELS = base64.b64encode(bytes([0, 1, 1, 0, 2, 0, 0, 1, 0, 0, 1, 2])).decode()
SIZE = {"width": 4, "height": 3}

# task_type -> (project settings, files to upload, result for the first item)
PROJECTS = {
    "classification": (
        {"labels": ["cat", "dog"], "mode": "multi"}, {"a.jpg": JPEG, "b.jpg": JPEG},
        {"labels": ["dog", "cat"]},
    ),
    "captioning": ({}, {"a.jpg": JPEG}, {"caption": "一只猫，\n在窗边。"}),
    "text_span": (
        {"labels": ["PER", "LOC"]}, {"a.txt": "🎉张三在北京".encode()},
        {"spans": [{"start": 1, "end": 3, "label": "PER"}, {"start": 4, "end": 6, "label": "LOC"}]},
    ),
    "detection": (
        {"categories": ["person", "car"]}, {"a.jpg": JPEG},
        {"image_size": SIZE, "boxes": [{"category": "car", "x": 0.5, "y": 0, "width": 2, "height": 1.5}]},
    ),
    "segmentation": ({"categories": ["road", "building"]}, {"a.jpg": JPEG}, {"image_size": SIZE, "pixels": PIXELS}),
    "polygon": (
        {"categories": ["material"]}, {"a.jpg": JPEG},
        {"image_size": SIZE, "base_revision": 0,
         "polygons": [{"category": "material", "points": [[0, 0], [4, 0], [4, 3], [0, 3]]}]},
    ),
    "depth": ({}, {"a.jpg": JPEG}, {"image_size": SIZE, "pixels": PIXELS}),
}


def make_project(tmp_path: Path, task_type: str):
    app = create_app(tmp_path / "ws", cors_origins=[], import_roots=[])
    settings, files, result = PROJECTS[task_type]
    created = create(app, task_type, task_type, settings)
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    for path, content in files.items():
        assert upload(app, project_id, path, content).status_code == 200
    item = request(app, "GET", f"/api/projects/{project_id}/queue").json()["items"][0]
    saved = request(app, "POST", f"/api/projects/{project_id}/annotations",
                    json={"item_id": item["item_id"], "result": result})
    assert saved.status_code == 200, saved.text
    return app, project_id


def export_files(response, tmp_path: Path) -> tuple[Path, ...]:
    """The downloaded export as files, unpacking a multi-file zip in order."""
    disposition = response.headers["content-disposition"]
    name = re.search(r'filename="?([^";]+)', disposition).group(1)
    target = tmp_path / "download"
    target.mkdir(exist_ok=True)
    if not name.endswith(".zip"):
        path = target / name
        path.write_bytes(response.content)
        return (path,)
    paths = []
    with zipfile.ZipFile(io.BytesIO(response.content)) as bundle:
        for info in bundle.infolist():
            path = target / info.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(bundle.read(info))
            paths.append(path)
    return tuple(paths)


def test_every_offered_format_has_exactly_one_contract():
    offered = {(spec.type, item.format) for spec in TASK_TYPE_SPECS for item in spec.export_formats}
    registered = {key for key in CONTRACTS if key[1] != "json"}
    assert offered == registered
    # Every contract is versioned and listed once by its id.
    assert all(re.fullmatch(r"[a-z-]+/v\d+", contract_id) for contract_id in BY_ID)


@pytest.mark.parametrize(
    ("task_type", "export_format"),
    sorted({key for key in CONTRACTS if key[0] != "reid"}),
)
def test_downloads_match_their_contract_and_are_deterministic(tmp_path, task_type, export_format):
    app, project_id = make_project(tmp_path, task_type)
    task_types = request(app, "GET", "/api/task-types").json()
    offered = {
        item["format"]: item["contract"]
        for spec in task_types if spec["type"] == task_type for item in spec["export_formats"]
    }
    contract = CONTRACTS[(task_type, export_format)]
    if export_format != "json":
        assert offered[export_format] == contract.id

    first = request(app, "GET", f"/api/projects/{project_id}/export", params={"format": export_format})
    assert first.status_code == 200, first.text
    assert first.headers[EXPORT_CONTRACT_HEADER] == contract.id
    validate_files(contract.id, export_files(first, tmp_path))
    second = request(app, "GET", f"/api/projects/{project_id}/export", params={"format": export_format})
    assert second.content == first.content


def test_reid_pairs_export_matches_its_contract(tmp_path):
    registry, _ = make_review_registry(tmp_path)
    app = create_app(registry)
    request(app, "POST", "/api/projects/lobby/annotations",
            json={"item_id": "c1", "result": {"label": "same", "notes": ""}})
    started = request(app, "POST", "/api/projects/lobby/actions/finalize", json={"options": {}})
    assert wait_for_action(app, "lobby", started.json()["id"])["state"] == "done"
    response = request(app, "GET", "/api/projects/lobby/export")
    assert response.headers[EXPORT_CONTRACT_HEADER] == "reid-pairs-csv/v1"
    (path,) = export_files(response, tmp_path)
    validate_files("reid-pairs-csv/v1", [path])
    assert "1" in path.read_text(encoding="utf-8").splitlines()[1].split(",")


def write(tmp_path: Path, name: str, value) -> Path:
    path = tmp_path / name
    path.write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")
    return path


def coco(info="detection-coco/v1", **changes):
    document = {
        "info": {"version": info},
        "images": [{"id": 1, "file_name": "a.jpg", "width": 4, "height": 3}],
        "categories": [{"id": 1, "name": "car"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 2, 1],
                         "area": 2.0, "iscrowd": 0}],
    }
    document.update(changes)
    return document


def polygon_coco(**annotation):
    base = {"id": 1, "image_id": 1, "category_id": 1, "segmentation": [[0, 0, 4, 0, 4, 3]],
            "area": 6.0, "bbox": [0.0, 0.0, 4.0, 3.0], "iscrowd": 0}
    base.update(annotation)
    return coco("polygon-coco/v1", annotations=[base])


@pytest.mark.parametrize(
    ("contract", "content", "message"),
    [
        ("detection-coco/v1", "{", "not UTF-8 JSON"),
        ("detection-coco/v1", coco(info="detection-coco/v2"), "info.version"),
        ("detection-coco/v1", coco(images=[{"id": 1, "file_name": "a.jpg", "width": 4.5, "height": 3}]), "positive integers"),
        ("detection-coco/v1", coco(annotations=[{"id": 1, "image_id": 2, "category_id": 1}]), "refer to an image"),
        ("detection-coco/v1", coco(annotations=[{"id": 1, "image_id": 1, "category_id": 1, "bbox": [0, 0, 2, 1], "area": 3, "iscrowd": 0}]), "width\\*height"),
        ("polygon-coco/v1", polygon_coco(area=5.0), "shoelace"),
        ("polygon-coco/v1", polygon_coco(bbox=[0, 0, 4, 2]), "bounding box"),
        ("polygon-coco/v1", polygon_coco(segmentation=[[0, 0, 4, 0]]), "at least 3 points"),
        ("polygon-coco/v1", polygon_coco(segmentation=[[0, 0, 5, 0, 4, 3]], bbox=[0, 0, 5, 3], area=7.5), "inside the image"),
        ("polygon-coco/v1", polygon_coco(iscrowd=1), "iscrowd"),
        ("text-span-json/v1", {"schema": 1, "history": [], "items": {"i": {
            "image_path": "a.txt", "length": 2, "spans": [{"start": 1, "end": 3, "label": "A"}]}}}, "start < end <= length"),
        ("polygon-json/v1", {"schema": 1, "history": [], "items": {"i": {
            "image_path": "a.jpg", "image_size": SIZE, "revision": 0, "polygons": []}}}, "revision"),
        ("classification-json/v1", {"schema": 2, "items": {}, "history": []}, "schema"),
        ("classification-csv/v1", "item_id,image_path,labels\ni1,a.jpg,cat\n", "JSON list"),
        ("caption-csv/v1", "id,path,caption\n", "header"),
        ("reid-pairs-csv/v1", "img1,img2,label,split,evidence,person_id1,person_id2,gap_sec\na,b,same,train,x,1,2,0\n", "0 or 1"),
    ],
)
def test_validators_reject_files_that_break_the_contract(tmp_path, contract, content, message):
    suffix = ".csv" if "csv" in contract else ".json"
    path = write(tmp_path, "export" + suffix, content)
    with pytest.raises(ContractViolation, match=message):
        validate_files(contract, [path])


def test_raster_exports_need_every_png_the_index_names(tmp_path):
    index = {"schema": 1, "history": [], "items": {"i1": {
        "image_path": "a.jpg", "image_size": SIZE, "mask_path": "i1.png", "pixel_hash": "x"}}}
    index_path = write(tmp_path, "index.json", index)
    with pytest.raises(ContractViolation, match="do not match the index"):
        validate_files("segmentation-masks/v1", [index_path])
    fake = tmp_path / "i1.png"
    fake.write_bytes(b"not a png")
    with pytest.raises(ContractViolation, match="not a PNG"):
        validate_files("segmentation-masks/v1", [index_path, fake])
    fake.write_bytes(b"\x89PNG\r\n\x1a\n...")
    validate_files("segmentation-masks/v1", [index_path, fake])
    with pytest.raises(ContractViolation, match="unknown export contract"):
        validate_files("segmentation-masks/v9", [index_path, fake])


# ----------------------------------------------------- the documentation


EXAMPLE = re.compile(r"<!-- export-example: (\S+) -->\s*```(\w+)\n(.*?)```", re.S)
COMMANDS = re.compile(r"<!-- export-commands -->\s*```bash\n(.*?)```", re.S)


def test_every_contract_has_a_documented_example_that_validates(tmp_path):
    text = INTERFACE_DOC.read_text(encoding="utf-8")
    examples = EXAMPLE.findall(text)
    assert {contract for contract, _, _ in examples} == set(BY_ID)
    for index, (contract_id, language, body) in enumerate(examples):
        contract = BY_ID[contract_id]
        if language == "json":
            contract.validate_document(json.loads(body))
        else:
            assert language == "csv", contract_id
            path = write(tmp_path, f"example-{index}.csv", body)
            contract.validate((path,))
        # The documentation table lists the contract with its summary.
        assert f"`{contract_id}`" in text


def test_documented_commands_run_as_written(tmp_path):
    """The CLI block in 70_外部接口.md, run line by line against a fixture project."""
    block = COMMANDS.search(INTERFACE_DOC.read_text(encoding="utf-8")).group(1)
    make_project(tmp_path, "detection")  # creates project "detection" in tmp_path/ws
    out = tmp_path / "out"
    out.mkdir()
    lines = [line for line in block.splitlines() if line.strip() and not line.startswith("#")]
    assert lines and all(line.startswith("python -m annotation_platform.exports") for line in lines)
    env = {"ANNOTATION_WORKSPACE": str(tmp_path / "ws"), "PATH": "/usr/bin:/bin"}
    for line in lines:
        argv = shlex.split(line.replace("/tmp/export", str(out)))
        completed = subprocess.run(
            [sys.executable, *argv[1:]], cwd=BACKEND, env=env, capture_output=True, text=True,
        )
        assert completed.returncode == 0, (line, completed.stderr)
    document = json.loads((out / "detection-coco.json").read_text(encoding="utf-8"))
    assert document["info"]["version"] == "detection-coco/v1"


def test_cli_reports_contract_violations_and_errors(tmp_path, capsys):
    make_project(tmp_path, "polygon")
    workspace = str(tmp_path / "ws")
    archive = tmp_path / "polygon.json"
    assert exports_main(["export", "--workspace", workspace, "polygon", "--output", str(archive)]) == 0
    assert capsys.readouterr().out.startswith("polygon-json/v1\t")
    assert exports_main(["check", "polygon-json/v1", str(archive)]) == 0
    assert exports_main(["check", "polygon-coco/v1", str(archive)]) == 1
    assert "invalid:" in capsys.readouterr().err
    assert exports_main(["export", "--workspace", workspace, "missing", "--output", str(archive)]) == 2
    assert exports_main(["export", "--workspace", workspace, "polygon", "--format", "voc",
                         "--output", str(archive)]) == 2
