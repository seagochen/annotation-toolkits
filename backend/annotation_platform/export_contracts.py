"""Versioned export contracts: what every task type's export files look like.

Each ``(task_type, format)`` a user can download maps to exactly one
``ExportContract``: a stable name (the ``ExportResult.format`` the module
returns), a version, and a validator that checks produced files against the
documented schema (docs/detailed_design/70_外部接口.md "导出契约"). The
workspace validates every export before it is downloaded, so a file that leaves
the platform always matches its contract; ``annotation_platform.exports check``
runs the same validators on files outside the platform.

A breaking change to a format (removing or renaming a field, changing a unit or
encoding) must bump its version; adding an optional field does not.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .file_kinds import is_image_content


class ContractViolation(ValueError):
    """An export file does not match its contract."""


@dataclass(frozen=True)
class ExportContract:
    name: str
    version: int
    # Validates the export's files, in the order the module returns them.
    validate: Callable[[tuple[Path, ...]], None]
    # Validates one document of the contract (the first file), for examples.
    validate_document: Callable[[object], None]
    summary: str

    @property
    def id(self) -> str:
        return f"{self.name}/v{self.version}"


# ----------------------------------------------------------- primitives


def _fail(where: str, problem: str) -> None:
    raise ContractViolation(f"{where}: {problem}")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _require(condition: bool, where: str, problem: str) -> None:
    if not condition:
        _fail(where, problem)


def _mapping(value: object, where: str, keys: Iterable[str]) -> dict:
    _require(isinstance(value, dict), where, "must be an object")
    missing = sorted(set(keys) - set(value))
    _require(not missing, where, f"missing fields {missing}")
    return value


def _size(value: object, where: str) -> dict:
    size = _mapping(value, where, ("width", "height"))
    _require(
        _is_int(size["width"]) and _is_int(size["height"])
        and size["width"] > 0 and size["height"] > 0,
        where, "width/height must be positive integers",
    )
    return size


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise ContractViolation(f"{path.name}: not UTF-8 JSON ({error})") from error


def _read_csv(path: Path, header: tuple[str, ...]) -> list[dict]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            fields = tuple(reader.fieldnames or ())
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise ContractViolation(f"{path.name}: not a UTF-8 CSV ({error})") from error
    _require(fields == header, path.name, f"header must be {list(header)}, got {list(fields)}")
    return rows


def _one_file(paths: tuple[Path, ...], name: str) -> Path:
    _require(len(paths) == 1, name, f"expected exactly one file, got {len(paths)}")
    return paths[0]


# ------------------------------------------------------- JSON sidecars


def _sidecar(item_check: Callable[[dict, str], None]) -> Callable[[object], None]:
    """The ``{schema: 1, items: {item_id: ...}, history: [...]}`` native document."""

    def validate(document: object) -> None:
        root = _mapping(document, "document", ("schema", "items", "history"))
        _require(root["schema"] == 1, "schema", "must be 1")
        _require(isinstance(root["items"], dict), "items", "must be an object keyed by item_id")
        _require(isinstance(root["history"], list), "history", "must be a list")
        for item_id, item in root["items"].items():
            where = f"items[{item_id!r}]"
            _mapping(item, where, ("image_path",))
            _require(isinstance(item["image_path"], str), where, "image_path must be text")
            item_check(item, where)
        for index, event in enumerate(root["history"]):
            where = f"history[{index}]"
            _mapping(event, where, ("item_id", "created_at"))
            _require(isinstance(event["item_id"], str), where, "item_id must be text")

    return validate


def _labels_item(item: dict, where: str) -> None:
    labels = _mapping(item, where, ("labels",))["labels"]
    _require(
        isinstance(labels, list) and all(isinstance(label, str) for label in labels),
        where, "labels must be a list of text",
    )


def _caption_item(item: dict, where: str) -> None:
    _require(isinstance(_mapping(item, where, ("caption",))["caption"], str), where, "caption must be text")


def _spans_item(item: dict, where: str) -> None:
    _mapping(item, where, ("length", "spans"))
    length = item["length"]
    _require(_is_int(length) and length >= 0, where, "length must be a non-negative integer")
    _require(isinstance(item["spans"], list), where, "spans must be a list")
    for index, span in enumerate(item["spans"]):
        at = f"{where}.spans[{index}]"
        _mapping(span, at, ("start", "end", "label"))
        _require(
            _is_int(span["start"]) and _is_int(span["end"])
            and 0 <= span["start"] < span["end"] <= length,
            at, "needs integer code point offsets with 0 <= start < end <= length",
        )
        _require(isinstance(span["label"], str), at, "label must be text")


def _boxes_item(item: dict, where: str) -> None:
    _mapping(item, where, ("image_size", "boxes"))
    _size(item["image_size"], f"{where}.image_size")
    _require(isinstance(item["boxes"], list), where, "boxes must be a list")
    for index, box in enumerate(item["boxes"]):
        at = f"{where}.boxes[{index}]"
        _mapping(box, at, ("category", "x", "y", "width", "height"))
        _require(isinstance(box["category"], str), at, "category must be text")
        _require(
            all(_is_number(box[key]) for key in ("x", "y", "width", "height")),
            at, "x/y/width/height must be finite numbers",
        )


def _polygon_points(points: object, where: str) -> None:
    _require(isinstance(points, list) and len(points) >= 3, where, "needs at least 3 points")
    for point in points:
        _require(
            isinstance(point, list) and len(point) == 2 and all(_is_number(value) for value in point),
            where, "points must be [x, y] pairs of finite numbers",
        )


def _polygons_item(item: dict, where: str) -> None:
    _mapping(item, where, ("image_size", "polygons", "revision"))
    _size(item["image_size"], f"{where}.image_size")
    _require(_is_int(item["revision"]) and item["revision"] >= 1, where, "revision must be >= 1")
    _require(isinstance(item["polygons"], list), where, "polygons must be a list")
    for index, polygon in enumerate(item["polygons"]):
        at = f"{where}.polygons[{index}]"
        _mapping(polygon, at, ("category", "points"))
        _require(isinstance(polygon["category"], str), at, "category must be text")
        _polygon_points(polygon["points"], at)


def _raster_item(file_key: str, optional: tuple[str, ...] = ()) -> Callable[[dict, str], None]:
    def check(item: dict, where: str) -> None:
        _mapping(item, where, ("image_size", file_key, "pixel_hash", *optional))
        _size(item["image_size"], f"{where}.image_size")
        _require(isinstance(item[file_key], str), where, f"{file_key} must be text")
        _require(isinstance(item["pixel_hash"], str), where, "pixel_hash must be text")
        for key in optional:
            _require(item[key] is None or isinstance(item[key], str), where, f"{key} must be text or null")

    return check


def _json_contract(document_check: Callable[[object], None]) -> Callable[[tuple[Path, ...]], None]:
    def validate(paths: tuple[Path, ...]) -> None:
        document_check(_read_json(_one_file(paths, "export")))

    return validate


def _raster_contract(
    document_check: Callable[[object], None], file_key: str
) -> Callable[[tuple[Path, ...]], None]:
    """``index.json`` followed by one PNG per item, as the index names them."""

    def validate(paths: tuple[Path, ...]) -> None:
        _require(bool(paths), "export", "expected index.json and its PNG files")
        index, *rasters = paths
        _require(index.name == "index.json", "export", "the first file must be index.json")
        document = _read_json(index)
        document_check(document)
        names = sorted(path.name for path in rasters)
        expected = sorted(item[file_key] for item in document["items"].values())
        _require(names == expected, "export", f"PNG files {names} do not match the index {expected}")
        for path in rasters:
            with path.open("rb") as handle:
                _require(
                    handle.read(8) == b"\x89PNG\r\n\x1a\n", path.name, "is not a PNG file"
                )

    return validate


# ------------------------------------------------------------------ CSV


def _labels_csv(paths: tuple[Path, ...]) -> None:
    path = _one_file(paths, "export")
    for number, row in enumerate(_read_csv(path, ("item_id", "image_path", "labels")), start=2):
        try:
            labels = json.loads(row["labels"])
        except ValueError:
            labels = None
        _require(
            isinstance(labels, list) and all(isinstance(label, str) for label in labels),
            f"{path.name} line {number}", "labels must be a JSON list of text",
        )


def _caption_csv(paths: tuple[Path, ...]) -> None:
    _read_csv(_one_file(paths, "export"), ("item_id", "image_path", "caption"))


def _reid_pairs_csv(paths: tuple[Path, ...]) -> None:
    from reid_annotation_tool.core import PAIR_FIELDS

    path = _one_file(paths, "export")
    for number, row in enumerate(_read_csv(path, PAIR_FIELDS), start=2):
        _require(row["label"] in {"0", "1"}, f"{path.name} line {number}", "label must be 0 or 1")


# ----------------------------------------------------------------- COCO


def _coco(name: str, annotation_check: Callable[[dict, str, dict], None]) -> Callable[[object], None]:
    """Shared COCO structure; ``info.version`` names the contract."""

    def validate(document: object) -> None:
        root = _mapping(document, "document", ("info", "images", "annotations", "categories"))
        info = _mapping(root["info"], "info", ("version",))
        _require(info["version"] == f"{name}/v1", "info.version", f"must be {name}/v1")
        for key in ("images", "annotations", "categories"):
            _require(isinstance(root[key], list), key, "must be a list")
        images: dict[int, dict] = {}
        for index, image in enumerate(root["images"]):
            where = f"images[{index}]"
            _mapping(image, where, ("id", "file_name", "width", "height"))
            _require(_is_int(image["id"]) and image["id"] not in images, where, "id must be a unique integer")
            _require(isinstance(image["file_name"], str), where, "file_name must be text")
            _size(image, where)
            images[image["id"]] = image
        categories: set[int] = set()
        for index, category in enumerate(root["categories"]):
            where = f"categories[{index}]"
            _mapping(category, where, ("id", "name"))
            _require(
                _is_int(category["id"]) and category["id"] not in categories,
                where, "id must be a unique integer",
            )
            _require(isinstance(category["name"], str), where, "name must be text")
            categories.add(category["id"])
        context = {"images": images, "categories": categories, "ids": set()}
        for index, annotation in enumerate(root["annotations"]):
            where = f"annotations[{index}]"
            _mapping(annotation, where, ("image_id",))
            _require(annotation["image_id"] in images, where, "image_id must refer to an image")
            annotation_check(annotation, where, context)

    return validate


def _object_annotation(annotation: dict, where: str, context: dict) -> None:
    _mapping(annotation, where, ("id", "category_id", "bbox", "area", "iscrowd"))
    _require(
        _is_int(annotation["id"]) and annotation["id"] not in context["ids"],
        where, "id must be a unique integer",
    )
    context["ids"].add(annotation["id"])
    _require(annotation["category_id"] in context["categories"], where, "category_id must refer to a category")
    _require(annotation["iscrowd"] == 0, where, "iscrowd must be 0")
    bbox = annotation["bbox"]
    _require(
        isinstance(bbox, list) and len(bbox) == 4 and all(_is_number(value) for value in bbox)
        and bbox[2] >= 0 and bbox[3] >= 0,
        where, "bbox must be [x, y, width, height] finite numbers",
    )
    _require(_is_number(annotation["area"]) and annotation["area"] >= 0, where, "area must be >= 0")


def _detection_annotation(annotation: dict, where: str, context: dict) -> None:
    _object_annotation(annotation, where, context)
    x, y, width, height = annotation["bbox"]
    _require(width > 0 and height > 0, where, "bbox must have a positive size")
    _require(math.isclose(annotation["area"], width * height, rel_tol=1e-9), where, "area must be width*height")


def _polygon_annotation(annotation: dict, where: str, context: dict) -> None:
    from .img_annotation.standard.polygon_task import bounding_box, shoelace_area

    _object_annotation(annotation, where, context)
    segmentation = annotation.get("segmentation")
    _require(
        isinstance(segmentation, list) and len(segmentation) == 1,
        where, "segmentation must hold exactly one polygon ring",
    )
    ring = segmentation[0]
    _require(
        isinstance(ring, list) and len(ring) >= 6 and len(ring) % 2 == 0
        and all(_is_number(value) for value in ring),
        where, "a ring is [x1, y1, x2, y2, ...] with at least 3 points",
    )
    points = [[x, y] for x, y in zip(ring[::2], ring[1::2])]
    image = context["images"][annotation["image_id"]]
    _require(
        all(0 <= x <= image["width"] and 0 <= y <= image["height"] for x, y in points),
        where, "polygon points must lie inside the image",
    )
    _require(
        math.isclose(annotation["area"], shoelace_area(points), rel_tol=1e-9, abs_tol=1e-9),
        where, "area must be the polygon's shoelace area",
    )
    _require(
        all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(annotation["bbox"], bounding_box(points))),
        where, "bbox must be the polygon's bounding box",
    )


def _mask_annotation(annotation: dict, where: str, context: dict) -> None:
    _require(
        isinstance(annotation.get("segmentation_mask"), str),
        where, "segmentation_mask must name the item's mask PNG",
    )


# ------------------------------------------------------------ registry


def _contract(name: str, summary: str, document: Callable[[object], None],
              files: Callable[[tuple[Path, ...]], None] | None = None) -> ExportContract:
    return ExportContract(name, 1, files or _json_contract(document), document, summary)


def _no_document(name: str) -> Callable[[object], None]:
    def validate(_document: object) -> None:
        raise ContractViolation(f"{name} is not a JSON document; validate its files instead")

    return validate


_SEGMENTATION_INDEX = _sidecar(_raster_item("mask_path"))
_DEPTH_INDEX = _sidecar(_raster_item("depth_path", ("baseline_path",)))
_DETECTION_COCO = _coco("detection-coco", _detection_annotation)
_SEGMENTATION_COCO = _coco("segmentation-coco", _mask_annotation)
_POLYGON_COCO = _coco("polygon-coco", _polygon_annotation)

_NATIVE = ("native", "json")

# (task_type, request format) -> contract. `native` and `json` are aliases.
CONTRACTS: dict[tuple[str, str], ExportContract] = {}


def _register(task_type: str, formats: tuple[str, ...], contract: ExportContract) -> None:
    for export_format in formats:
        CONTRACTS[(task_type, export_format)] = contract


_register("classification", _NATIVE, _contract(
    "classification-json", "JSON 侧车：items[item_id] = {image_path, labels}", _sidecar(_labels_item)))
_register("classification", ("csv",), _contract(
    "classification-csv", "CSV：item_id,image_path,labels（labels 为 JSON 数组）",
    _no_document("classification-csv"), _labels_csv))
_register("captioning", _NATIVE, _contract(
    "caption-json", "JSON 侧车：items[item_id] = {image_path, caption}", _sidecar(_caption_item)))
_register("captioning", ("csv",), _contract(
    "caption-csv", "CSV：item_id,image_path,caption", _no_document("caption-csv"), _caption_csv))
_register("text_span", _NATIVE, _contract(
    "text-span-json", "JSON 侧车：items[item_id] = {image_path, length, spans}（code point offset）",
    _sidecar(_spans_item)))
_register("detection", _NATIVE, _contract(
    "detection-json", "JSON 侧车：items[item_id] = {image_path, image_size, boxes}",
    _sidecar(_boxes_item)))
_register("detection", ("coco",), _contract(
    "detection-coco", "COCO 检测：bbox 为 [x, y, w, h] 原图像素", _DETECTION_COCO))
_register("segmentation", _NATIVE, _contract(
    "segmentation-masks", "index.json + 每个 item 一张 8-bit 灰度 PNG（像素值 = 类别序号，0 为背景）",
    _SEGMENTATION_INDEX, _raster_contract(_SEGMENTATION_INDEX, "mask_path")))
_register("segmentation", ("coco",), _contract(
    "segmentation-coco", "COCO 兼容：annotations 以 segmentation_mask 引用掩膜 PNG",
    _SEGMENTATION_COCO))
_register("polygon", _NATIVE, _contract(
    "polygon-json", "JSON 侧车：items[item_id] = {image_path, image_size, polygons, revision}",
    _sidecar(_polygons_item)))
_register("polygon", ("coco",), _contract(
    "polygon-coco", "标准 COCO 多边形：segmentation 环、鞋带公式 area、bbox", _POLYGON_COCO))
_register("depth", _NATIVE, _contract(
    "depth-maps", "index.json + 每个 item 一张 8-bit 灰度深度 PNG",
    _DEPTH_INDEX, _raster_contract(_DEPTH_INDEX, "depth_path")))
_register("reid", ("native",), _contract(
    "reid-pairs-csv", "CSV：当前 pairs 清单（PAIR_FIELDS，label 为 0/1）",
    _no_document("reid-pairs-csv"), _reid_pairs_csv))

BY_ID: dict[str, ExportContract] = {contract.id: contract for contract in CONTRACTS.values()}


def contract_for(task_type: str, export_format: str) -> ExportContract | None:
    return CONTRACTS.get((task_type, export_format))


def coco_info(name: str) -> dict:
    """The ``info`` block a COCO export carries; its version names the contract."""
    return {"description": f"Annotation Toolkits {name} export", "version": f"{name}/v1"}


def validate_files(contract_id: str, paths: Iterable[Path]) -> ExportContract:
    contract = BY_ID.get(contract_id)
    if contract is None:
        raise ContractViolation(f"unknown export contract {contract_id!r}; known: {sorted(BY_ID)}")
    contract.validate(tuple(Path(path) for path in paths))
    return contract
