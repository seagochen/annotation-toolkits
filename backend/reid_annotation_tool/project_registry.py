"""Local multi-project registry built on the existing project config loader.

The registry owns only platform metadata and a reference to each project's
``reid.yaml``. Dataset paths and all task settings remain owned by that file,
so there is one validation and relative-path implementation for both the CLI
and the future HTTP service.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

ENTRY_KEYS = frozenset({"id", "name", "task_type", "config"})
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

if TYPE_CHECKING:
    from annotation_platform.task_types import (
        TaskProject,
        TaskTypeModule,
        TaskTypeRegistry,
    )


class RegistryError(RuntimeError):
    """The registry is malformed or cannot safely identify a project."""


@dataclass(frozen=True)
class RegisteredProject:
    id: str
    name: str
    config_path: Path
    project: "TaskProject"
    module: "TaskTypeModule"

    @property
    def task_type(self) -> str:
        return self.module.type_name

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "root": str(self.project.root),
            "status": self.module.status(self.project).state,
        }


class ProjectRegistry:
    """Validated, deterministic collection of locally configured projects."""

    def __init__(self, entries: list[RegisteredProject], path: Path):
        self.path = path
        self._entries = {entry.id: entry for entry in entries}

    @classmethod
    def load(
        cls,
        path: str | Path,
        task_types: "TaskTypeRegistry | None" = None,
    ) -> "ProjectRegistry":
        if task_types is None:
            from annotation_platform.task_types import default_task_types

            task_types = default_task_types()
        source = Path(path).expanduser().resolve()
        raw_entries = _read_entries(source)
        entries: list[RegisteredProject] = []
        ids: set[str] = set()
        roots: dict[Path, str] = {}
        for raw, owner in raw_entries:
            entry = _load_entry(raw, owner, task_types)
            if entry.id in ids:
                raise RegistryError(f"{source}: duplicate project id {entry.id!r}")
            ids.add(entry.id)
            root = entry.project.root.resolve()
            if root in roots:
                raise RegistryError(
                    f"{source}: projects {roots[root]!r} and {entry.id!r} "
                    f"share dataset root {root}"
                )
            roots[root] = entry.id
            entries.append(entry)
        return cls(entries, source)

    def list_projects(self) -> list[dict]:
        return [entry.describe() for entry in self._entries.values()]

    def get_entry(self, project_id: str) -> RegisteredProject:
        """Return platform metadata and the loaded task project together."""
        try:
            return self._entries[project_id]
        except KeyError:
            raise RegistryError(
                f"unknown project {project_id!r}; available: {sorted(self._entries)}"
            ) from None

    def load_project(self, project_id: str) -> object:
        """Return the module-owned value for compatibility with existing callers."""
        return self.get_entry(project_id).project.value


def _read_entries(source: Path) -> list[tuple[dict, Path]]:
    if source.is_dir():
        files = sorted({*source.glob("*.yaml"), *source.glob("*.yml")})
        if not files:
            raise RegistryError(f"{source}: no project entry YAML files")
        return [(_read_mapping(path), path) for path in files]
    if not source.is_file():
        raise RegistryError(f"registry not found: {source}")
    raw = _read_mapping(source)
    if set(raw) != {"projects"}:
        raise RegistryError(f"{source}: expected only a `projects` list")
    projects = raw["projects"]
    if not isinstance(projects, list):
        raise RegistryError(f"{source}: `projects` must be a list")
    return [(entry, source) for entry in projects]


def _read_mapping(path: Path) -> dict:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise RegistryError(f"cannot read {path}: {error}") from error
    if not isinstance(raw, dict):
        raise RegistryError(f"{path}: entry must be a YAML mapping")
    return raw


def _required_text(raw: dict, key: str, owner: Path) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{owner}: `{key}` must be a non-empty string")
    return value.strip()


def _load_entry(
    raw: object, owner: Path, task_types: "TaskTypeRegistry"
) -> RegisteredProject:
    if not isinstance(raw, dict):
        raise RegistryError(f"{owner}: each project entry must be a mapping")
    unknown = sorted(set(raw) - ENTRY_KEYS)
    if unknown:
        raise RegistryError(f"{owner}: unknown project keys {unknown}; known: {sorted(ENTRY_KEYS)}")
    project_id = _required_text(raw, "id", owner)
    if not PROJECT_ID.fullmatch(project_id):
        raise RegistryError(f"{owner}: invalid project id {project_id!r}")
    name = _required_text(raw, "name", owner)
    task_type = _required_text(raw, "task_type", owner)
    from annotation_platform.task_types import UnknownTaskTypeError

    try:
        module = task_types.require(task_type)
    except UnknownTaskTypeError as error:
        raise RegistryError(f"{owner}: {error}") from error
    configured_path = Path(_required_text(raw, "config", owner)).expanduser()
    config_path = (
        configured_path
        if configured_path.is_absolute()
        else (owner.parent / configured_path).resolve()
    )
    if not config_path.is_file():
        raise RegistryError(f"{owner}: project config not found: {config_path}")
    from annotation_platform.task_types import TaskOperationError

    try:
        project = module.load(config_path)
    except TaskOperationError as error:
        raise RegistryError(f"{owner}: invalid project config: {error}") from error
    return RegisteredProject(project_id, name, config_path, project, module)
