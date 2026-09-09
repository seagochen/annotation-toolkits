from pathlib import Path

import pytest

from reid_annotation_tool.contract import Pipeline
from reid_annotation_tool.registry import ModelRegistry, RegistryError


def make_registry(tmp_path, name="detector", content=b"weights") -> ModelRegistry:
    weights = tmp_path / f"{name}.pt"
    weights.write_bytes(content)
    return ModelRegistry({name: {"path": str(weights), "framework": "pytorch",
                                 "device": "cpu", "kind": ""}})


def test_resolve_path_and_digest(tmp_path):
    registry = make_registry(tmp_path)
    assert registry.resolve_path("detector") == tmp_path / "detector.pt"
    digest = registry.digest("detector")
    assert len(digest) == 16
    # Cached: asking twice returns the same value without re-reading the file.
    assert registry.digest("detector") == digest


def test_unknown_name_is_reported(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(RegistryError, match="unknown model"):
        registry.resolve_path("nope")


def test_describe_is_manifest_ready(tmp_path):
    registry = make_registry(tmp_path)
    described = registry.describe("detector")
    assert described["name"] == "detector"
    assert described["framework"] == "pytorch"
    assert len(described["sha256"]) == 64


def test_a_three_parameter_script_never_receives_a_registry(tmp_path):
    """Every existing pipeline script keeps working unmodified."""
    script = tmp_path / "old.py"
    script.write_text(
        "from reid_annotation_tool.contract import Observation\n"
        "def process_frame(image, source, config):\n"
        "    return [Observation([0, 0, 1, 1])]\n", encoding="utf-8")
    pipeline = Pipeline.load(str(script), registry=make_registry(tmp_path))
    found = pipeline.process_frame(None, None)
    assert len(found) == 1


def test_a_four_parameter_script_receives_the_registry(tmp_path):
    script = tmp_path / "new.py"
    script.write_text(
        "from reid_annotation_tool.contract import Observation\n"
        "def process_frame(image, source, config, registry):\n"
        "    path = registry.resolve_path('detector')\n"
        "    return [Observation([0, 0, 1, 1], class_id=len(str(path)))]\n",
        encoding="utf-8")
    registry = make_registry(tmp_path)
    pipeline = Pipeline.load(str(script), registry=registry)
    found = pipeline.process_frame(None, None)
    assert found[0].class_id == len(str(registry.resolve_path("detector")))


def test_open_source_also_receives_the_registry_when_declared(tmp_path):
    """This is where the bundled ultralytics pipeline actually builds its
    detector, so registry-awareness has to reach open_source too."""
    script = tmp_path / "opens.py"
    script.write_text(
        "from reid_annotation_tool.contract import Observation\n"
        "seen = {}\n"
        "def open_source(source, config, registry):\n"
        "    seen['path'] = str(registry.resolve_path('detector'))\n"
        "def process_frame(image, source, config):\n"
        "    return [Observation([0, 0, 1, 1], class_id=len(seen.get('path', '')))]\n",
        encoding="utf-8")
    registry = make_registry(tmp_path)
    pipeline = Pipeline.load(str(script), registry=registry)
    pipeline.open_source(None)
    found = pipeline.process_frame(None, None)
    assert found[0].class_id == len(str(registry.resolve_path("detector")))


def test_registry_never_enters_the_serialized_config(tmp_path):
    """describe() feeds manifest.json via atomic_write_json; a live registry
    object in `config` would break that JSON serialization."""
    script = tmp_path / "plain.py"
    script.write_text(
        "from reid_annotation_tool.contract import Observation\n"
        "def process_frame(image, source, config):\n"
        "    return []\n", encoding="utf-8")
    pipeline = Pipeline.load(str(script), {"a": 1}, make_registry(tmp_path))
    import json
    json.dumps(pipeline.describe())  # must not raise
