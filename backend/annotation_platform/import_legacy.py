"""Convert an offline app-only export to native task workspaces, without overwrites."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

import yaml
from local_files import atomic_write_json

from .hosted import hosted_task_types, user_workspace
from .img_annotation.common.image_dataset import item_id
from .img_annotation.standard.polygon_task import PolygonStore
from .task_types import Submission
from .workspace import Workspace

ROW_KINDS = ("datasets", "labels", "assets", "tasks", "suggestion_jobs", "suggestions")
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}


def _json(file: Path):
    if file.is_symlink() or not file.is_file():
        raise ValueError("Migration inputs must be regular files")
    return json.loads(file.read_text(encoding="utf8"))


def _rows(user: dict, kind: str) -> dict:
    rows = user.get(kind)
    if not isinstance(rows, list):
        raise ValueError("Invalid migration rows")
    result = {}
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
            or not IDENTIFIER.fullmatch(row["id"]) or row["id"] in result
            or row.get("user_id") != user["userId"]):
            raise ValueError("Invalid migration identity or ownership")
        result[row["id"]] = row
    return result


def _polygons(objects: list, labels: dict) -> list:
    converted = []
    identifiers = set()
    for obj in objects:
        if obj.get("id") in identifiers or obj.get("labelId") not in labels:
            raise ValueError("Invalid legacy annotation object or label")
        identifiers.add(obj.get("id"))
        geometry = obj["geometry"]
        if geometry["kind"] == "bbox":
            x, y, w, h = (geometry[key] for key in ("x", "y", "width", "height"))
            if w <= 0 or h <= 0:
                raise ValueError("Invalid legacy bounding box")
            points = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        elif geometry["kind"] == "polygon":
            points = [[point["x"], point["y"]] for point in geometry["points"]]
        else:
            # No rasterisation or projection can preserve keypoint visibility
            # or instance masks in a polygon task. Never discard those records.
            raise ValueError("Legacy mask/keypoint annotations require a native task adapter before cutover")
        converted.append({"category": labels[obj["labelId"]], "points": points})
    return converted


def _convert_user(source: Path, root: Path, user: dict) -> dict:
    rows = {kind: _rows(user, kind) for kind in ROW_KINDS}
    datasets = rows["datasets"]
    for kind in ROW_KINDS[1:]:
        if any(row.get("dataset_id") not in datasets for row in rows[kind].values()):
            raise ValueError("Migration contains a foreign or missing dataset")
    if any(row["status"] not in {"success", "failed", "cancelled"} for row in rows["suggestion_jobs"].values()):
        raise ValueError("Drain Annotation AI jobs before migration")
    for task in rows["tasks"].values():
        asset = rows["assets"].get(task["asset_id"])
        if asset is None or asset["dataset_id"] != task["dataset_id"]:
            raise ValueError("Migration task references a foreign or missing asset")
    for job in rows["suggestion_jobs"].values():
        task = rows["tasks"].get(job["task_id"])
        if task is None or task["dataset_id"] != job["dataset_id"]:
            raise ValueError("Migration job references a foreign or missing task")
    for suggestion in rows["suggestions"].values():
        job = rows["suggestion_jobs"].get(suggestion["job_id"])
        if job is None or any(suggestion[key] != job[key] for key in ("dataset_id", "task_id")):
            raise ValueError("Migration suggestion references a foreign or missing job")
    workspace = Workspace(root, hosted_task_types(), import_roots=[])
    entries, mapping = [], {}
    for dataset in datasets.values():
        if dataset.get("deleted_at"):
            continue
        labels = [label for label in rows["labels"].values() if label["dataset_id"] == dataset["id"]]
        names = [label["name"] for label in labels]
        categories = {label["id"]: label["name"] if names.count(label["name"]) == 1 else
                      label["name"] + " [" + label["id"] + "]" for label in labels}
        project_id = dataset["id"]
        directory = workspace.projects_dir / project_id
        data = directory / "data"
        data.mkdir(parents=True)
        config = {"dataset": "./data", "categories": list(categories.values()) or ["未分类"],
                  "prelabels": ".annotations/legacy-prelabels.json"}
        config_path = directory / "config.yaml"
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf8")
        project = workspace.task_types.require("polygon").load(config_path).value
        store = PolygonStore(project)
        files = {}
        for asset in rows["assets"].values():
            if asset["dataset_id"] != dataset["id"]:
                continue
            checksum = asset.get("sha256", "")
            if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
                raise ValueError("Missing asset checksum")
            file = source / "objects" / checksum
            if file.is_symlink() or not file.is_file() or file.resolve() != file:
                raise ValueError("Invalid migration asset file")
            suffix = SUFFIXES.get(asset["mime_type"])
            if suffix is None:
                raise ValueError("Unsupported migration asset content type")
            relative = asset["id"] + suffix
            target = data / relative
            shutil.copyfile(file, target)
            if target.stat().st_size != asset["size_bytes"] or hashlib.sha256(target.read_bytes()).hexdigest() != checksum:
                raise ValueError("Migration asset checksum mismatch")
            files[asset["id"]] = relative
        prelabels = {"images": [], "annotations": [], "categories": [
            {"id": index + 1, "name": name} for index, name in enumerate(config["categories"])]}
        atomic_write_json(project.prelabels, prelabels)
        category_ids = {category["name"]: category["id"] for category in prelabels["categories"]}
        state = {"schema": 1, "items": {}, "history": []}
        mapping[dataset["id"]] = {"projectId": project_id, "taskType": "polygon", "assets": files, "tasks": {}}
        seen = set()
        for task in rows["tasks"].values():
            if task["dataset_id"] != dataset["id"]:
                continue
            asset = rows["assets"][task["asset_id"]]
            relative = files[asset["id"]]
            if relative in seen or not isinstance(task["revision"], int) or task["revision"] < 1:
                raise ValueError("Duplicate asset task or invalid revision")
            seen.add(relative)
            doc = json.loads(task["annotation_json"])
            size = {"width": asset["width"], "height": asset["height"]}
            if doc.get("version") != 1 or doc.get("assetId") != asset["id"] or doc.get("image") != size or not isinstance(doc.get("objects"), list):
                raise ValueError("Invalid migration annotation document")
            polygons = _polygons(doc["objects"], categories)
            key = item_id(relative)
            # Reuse native validation, then record the legacy revision/timestamp.
            validated = store.submit(Submission(key, {"base_revision": 0, "image_size": size, "polygons": polygons}))
            if task["status"] == "completed":
                saved = {name: validated[name] for name in ("image_path", "image_size", "polygons")}
                saved["revision"] = task["revision"]
                state["items"][key] = saved
                state["history"].append({"item_id": key, **saved, "base_revision": max(0, task["revision"] - 1),
                                         "created_at": task["updated_at"]})
            elif task["status"] in {"pending", "in_progress"}:
                image_id = len(prelabels["images"]) + 1
                prelabels["images"].append({"id": image_id, "file_name": relative, **size})
                for polygon in polygons:
                    prelabels["annotations"].append({"id": len(prelabels["annotations"]) + 1,
                        "image_id": image_id, "category_id": category_ids[polygon["category"]],
                        "segmentation": [[coordinate for point in polygon["points"] for coordinate in point]]})
            else:
                raise ValueError("Invalid legacy task status")
            # The next task must not see a temporary native submission.
            atomic_write_json(project.annotations, {"schema": 1, "items": {}, "history": []})
            mapping[dataset["id"]]["tasks"][task["id"]] = {"itemId": key, "assetId": asset["id"], "imagePath": relative,
                "legacyStatus": task["status"], "legacyRevision": task["revision"]}
        atomic_write_json(project.annotations, state)
        atomic_write_json(project.prelabels, prelabels)
        entries.append({"id": project_id, "name": dataset["title"], "task_type": "polygon",
                        "config": f"projects/{project_id}/config.yaml"})
    workspace._write_registry(entries)
    registry = workspace.load_registry()
    registry.list_projects()
    for entry in registry.entries():
        summary = entry.module.status(entry.project).details
        if summary.get("prelabel_error") or summary.get("prelabel_issue_count", 0):
            raise ValueError("Converted prelabels failed native validation")
    atomic_write_json(root / "legacy-records.json", user)
    atomic_write_json(root / "legacy-mapping.json", mapping)
    return {"projects": len(entries), "tasks": sum(len(item["tasks"]) for item in mapping.values()),
            "assets": sum(len(item["assets"]) for item in mapping.values())}


def import_legacy(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    if (source / "objects").is_symlink() or not (source / "objects").is_dir():
        raise ValueError("Migration objects must be an owned directory")
    bundle = _json(source / "export.json")
    if bundle.get("schemaVersion") != 1 or bundle.get("moduleId") != "annotation" or not isinstance(bundle.get("users"), list):
        raise ValueError("Invalid Annotation export")
    checksum = hashlib.sha256(json.dumps(bundle, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    destination = destination.absolute()
    resolved = destination.resolve()
    if resolved == source or resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise ValueError("Migration directories must not overlap")
    receipt = destination / "legacy-import.json"
    if receipt.is_file():
        prior = _json(receipt)
        if prior.get("sourceDigest") != checksum:
            raise ValueError("Destination contains a different migration")
        return {**prior, "replay": True}
    if destination.exists():
        raise ValueError("Migration requires a new destination")
    stage = Path(tempfile.mkdtemp(prefix=".annotation-import-", dir=destination.parent))
    report = {"sourceDigest": checksum, "users": 0, "projects": 0, "tasks": 0, "assets": 0,
              "sourceCounts": {kind: 0 for kind in ROW_KINDS}}
    seen = set()
    try:
        for user in bundle["users"]:
            user_id = user.get("userId")
            if not isinstance(user_id, str) or not 0 < len(user_id) <= 200 or user_id in seen:
                raise ValueError("Invalid migration owner")
            seen.add(user_id)
            result = _convert_user(source, user_workspace(stage, user_id), user)
            report["users"] += 1
            for key, count in result.items():
                report[key] += count
            for kind in ROW_KINDS:
                report["sourceCounts"][kind] += len(user[kind])
        if bundle.get("counts") != report["sourceCounts"]:
            raise ValueError("Migration source counts do not match")
        atomic_write_json(stage / "legacy-import.json", report)
        stage.rename(destination)
        return report
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(import_legacy(args.source, args.destination)))


if __name__ == "__main__":
    main()
