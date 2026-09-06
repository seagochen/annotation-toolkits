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

import yaml

from . import config as project_config

SUPPORTED_TASK_TYPES = frozenset({"reid"})
ENTRY_KEYS = frozenset({"id", "name", "task_type", "config"})
PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RegistryError(RuntimeError):
    """The registry is malformed or cannot safely identify a project."""


@dataclass(frozen=True)
class RegisteredProject:
    id: str
    name: str
    task_type: str
    config_path: Path
    project: project_config.Project

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "task_type": self.task_type,
            "root": str(self.project.dataset),
            "status": project_status(self.project),
        }


def project_status(project: project_config.Project) -> str:
    """A small task-independent status vocabulary for the project list."""
    status = project.summary()
    if not status["exists"]:
        return "missing"
    if not status["identities"]:
        return "empty"
    if not status["live_round"]:
        return "needs_mining"
    return "reviewing" if status["pending"] else "reviewed"


class ProjectRegistry:
    """Validated, deterministic collection of locally configured projects."""

    def __init__(self, entries: list[RegisteredProject], path: Path):
        self.path = path
        self._entries = {entry.id: entry for entry in entries}

    @classmethod
    def load(cls, path: str | Path) -> "ProjectRegistry":
        source = Path(path).expanduser().resolve()
        raw_entries = _read_entries(source)
        entries: list[RegisteredProject] = []
        ids: set[str] = set()
        roots: dict[Path, str] = {}
        for raw, owner in raw_entries:
            entry = _load_entry(raw, owner)
            if entry.id in ids:
                raise RegistryError(f"{source}: duplicate project id {entry.id!r}")
            ids.add(entry.id)
            root = entry.project.dataset.resolve()
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

    def load_project(self, project_id: str) -> project_config.Project:
        return self.get_entry(project_id).project


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


def _load_entry(raw: object, owner: Path) -> RegisteredProject:
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
    if task_type not in SUPPORTED_TASK_TYPES:
        raise RegistryError(
            f"{owner}: unknown task type {task_type!r}; supported: {sorted(SUPPORTED_TASK_TYPES)}"
        )
    config_path = project_config.resolve(owner.parent, _required_text(raw, "config", owner))
    if not config_path.is_file():
        raise RegistryError(f"{owner}: project config not found: {config_path}")
    try:
        project = project_config.load(config_path)
    except project_config.ConfigError as error:
        raise RegistryError(f"{owner}: invalid project config: {error}") from error
    if project.dataset.exists() and not project.dataset.is_dir():
        raise RegistryError(f"{owner}: dataset root is not a directory: {project.dataset}")
    return RegisteredProject(project_id, name, task_type, config_path, project)
