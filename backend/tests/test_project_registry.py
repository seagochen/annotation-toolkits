from pathlib import Path

import pytest

from reid_annotation_tool.project_registry import ProjectRegistry, RegistryError


def project_config(path: Path, dataset: str = "./dataset") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"dataset: {dataset}\npipeline:\n  script: tracking_csv\n",
        encoding="utf-8",
    )
    return path


def registry_file(path: Path, entries: str) -> Path:
    path.write_text(f"projects:\n{entries}", encoding="utf-8")
    return path


def entry(project_id: str, name: str, config: str, task_type: str = "reid") -> str:
    return (
        f"  - id: {project_id}\n"
        f"    name: {name}\n"
        f"    task_type: {task_type}\n"
        f"    config: {config}\n"
    )


def test_list_and_load_projects_reuse_project_config_paths(tmp_path):
    first = project_config(tmp_path / "one" / "reid.yaml")
    second = project_config(tmp_path / "two" / "reid.yaml")
    (first.parent / "dataset").mkdir()
    registry = ProjectRegistry.load(
        registry_file(
            tmp_path / "projects.yaml",
            entry("one", "One", "./one/reid.yaml")
            + entry("two", "Two", "./two/reid.yaml"),
        )
    )
    assert registry.list_projects() == [
        {
            "id": "one",
            "name": "One",
            "task_type": "reid",
            "root": str(first.parent / "dataset"),
            "status": "empty",
        },
        {
            "id": "two",
            "name": "Two",
            "task_type": "reid",
            "root": str(second.parent / "dataset"),
            "status": "missing",
        },
    ]
    assert registry.load_project("one").path == first


def test_directory_registry_uses_each_entry_as_its_relative_path_base(tmp_path):
    entries = tmp_path / "projects"
    config = project_config(entries / "scene" / "reid.yaml")
    entries.mkdir(exist_ok=True)
    (entries / "scene.yaml").write_text(
        "id: scene\n"
        "name: Scene\n"
        "task_type: reid\n"
        "config: ./scene/reid.yaml\n",
        encoding="utf-8",
    )
    registry = ProjectRegistry.load(entries)
    assert registry.load_project("scene").path == config


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (
            entry("same", "First", "./one.yaml")
            + entry("same", "Second", "./two.yaml"),
            "duplicate project id",
        ),
        (entry("other", "Other", "./one.yaml", "not-registered"), "unknown task type"),
        (entry("missing", "Missing", "./absent.yaml"), "project config not found"),
    ],
)
def test_invalid_registry_entries_are_rejected(tmp_path, entries, message):
    project_config(tmp_path / "one.yaml", "./one-dataset")
    project_config(tmp_path / "two.yaml", "./two-dataset")
    with pytest.raises(RegistryError, match=message):
        ProjectRegistry.load(registry_file(tmp_path / "projects.yaml", entries))


def test_duplicate_and_non_directory_roots_are_rejected(tmp_path):
    project_config(tmp_path / "one.yaml", "./shared")
    project_config(tmp_path / "two.yaml", "./shared")
    with pytest.raises(RegistryError, match="share dataset root"):
        ProjectRegistry.load(
            registry_file(
                tmp_path / "projects.yaml",
                entry("one", "One", "./one.yaml") + entry("two", "Two", "./two.yaml"),
            )
        )

    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("data", encoding="utf-8")
    project_config(tmp_path / "invalid.yaml", "./not-a-directory")
    with pytest.raises(RegistryError, match="dataset root is not a directory"):
        ProjectRegistry.load(
            registry_file(
                tmp_path / "invalid-projects.yaml",
                entry("invalid", "Invalid", "./invalid.yaml"),
            )
        )


def test_unknown_project_and_malformed_registry_have_stable_errors(tmp_path):
    project_config(tmp_path / "one.yaml")
    registry = ProjectRegistry.load(
        registry_file(tmp_path / "projects.yaml", entry("one", "One", "./one.yaml"))
    )
    with pytest.raises(RegistryError, match="unknown project 'missing'"):
        registry.load_project("missing")
    with pytest.raises(RegistryError, match="`projects` must be a list"):
        ProjectRegistry.load(registry_file(tmp_path / "bad.yaml", "  key: value\n"))


def test_invalid_project_config_and_empty_directory_are_rejected(tmp_path):
    (tmp_path / "invalid.yaml").write_text(
        "pipeline:\n  script: tracking_csv\n", encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="invalid project config"):
        ProjectRegistry.load(
            registry_file(
                tmp_path / "projects.yaml",
                entry("invalid", "Invalid", "./invalid.yaml"),
            )
        )
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RegistryError, match="no project entry YAML files"):
        ProjectRegistry.load(empty)
