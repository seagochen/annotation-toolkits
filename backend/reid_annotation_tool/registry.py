"""Named PyTorch checkpoints a pipeline script may look up instead of hardcoding.

This solves one narrow problem: "which name maps to which validated, hashed
file" -- declared once in `models:` (see config.py) instead of copy-pasted as
a literal path into every pipeline script and every trainer/evaluator config
that needs the same weights. It does **not** replace a pipeline script's own
`_state`/`_setup` singleton -- that caches a *constructed, parameter-configured*
runtime object (a `Detector` with its `imgsz`/`conf`/`device`), which varies
per script and even per call; a registry that also cached instances would
just be reimplementing that under a different name.

v1 is PyTorch-only (`framework: "pytorch"`), matching how models are actually
declared today: multi-framework support (ONNX, etc.) is a deliberate
non-goal here, not an oversight.
"""

from __future__ import annotations

from pathlib import Path

from .core import sha256


class RegistryError(RuntimeError):
    """An unknown model name was asked for, or its file has gone missing."""


class ModelRegistry:
    """A project's `models:` section, resolved once and reused everywhere.

    Holds no PyTorch state until `load_checkpoint` is actually called --
    constructing this costs nothing more than the config dict already did, so
    every project can carry one even when nothing uses it (see
    `Project.models()`, which returns `{}` when the section is absent).
    """

    def __init__(self, entries: dict[str, dict]):
        self.entries = entries
        self._digests: dict[str, str] = {}

    @classmethod
    def from_project(cls, project) -> "ModelRegistry":
        return cls(project.models())

    def _entry(self, name: str) -> dict:
        try:
            return self.entries[name]
        except KeyError:
            raise RegistryError(
                f"unknown model {name!r}; declared: {sorted(self.entries)}") from None

    def resolve_path(self, name: str) -> Path:
        return Path(self._entry(name)["path"])

    def digest(self, name: str) -> str:
        """A short, stable identity for the file's current bytes, cached per name."""
        if name not in self._digests:
            self._digests[name] = sha256(self.resolve_path(name))[:16]
        return self._digests[name]

    def load_checkpoint(self, name: str):
        """The raw `torch.load` result -- a state dict, or whatever the file holds.

        Imported lazily: a project that never calls this never needs torch
        installed just to load its config.
        """
        import torch

        entry = self._entry(name)
        return torch.load(self.resolve_path(name), map_location=entry["device"])

    def describe(self, name: str) -> dict:
        """Manifest-ready provenance for one named model."""
        entry = self._entry(name)
        return {"name": name, "path": entry["path"], "sha256": sha256(self.resolve_path(name)),
                "framework": entry["framework"]}
