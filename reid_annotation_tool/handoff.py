"""Hand a reviewed dataset to somebody else's trainer, and record what was fed.

Nothing here trains anything -- there is no model, no loss and no optimiser in
this package, on purpose. This module is the handoff: it refuses to start on a
dataset whose identity logic contradicts itself, tells the trainer where the
dataset and the reviewed pair manifest are, launches it, and writes down the
exact bytes it consumed so a checkpoint can be traced back to the annotations
that produced it.

The interface is four keys, and they are the only ones this tool writes into
the trainer's config (see ``OWNED``): the dataset root, the pair manifest, and
where the run should land. Backbones, objectives, learning rates and epochs are
the trainer's business, not the dataset's -- they come from the user's own
trainer config (``train.base_config``) and ``train.set`` overrides, and this
tool neither defines defaults for them nor validates them. A dataset tool that
shipped an opinion about somebody else's optimiser would be wrong for every
deployment but one, and would quietly rot as their trainer moved on.
"""

from __future__ import annotations

import json
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from . import conflicts as conflict_engine
from .core import atomic_write_json, read_csv, sha256

TASK_DIRECTORY = {"reid": "reid", "cls": "cls", "both": "both"}

# The only values this tool writes into the trainer's config, because it is the
# only party that knows them. They are also refused as ``train.set`` overrides:
# a run that trained on a different dataset than its manifest records would make
# the whole provenance chain a lie.
OWNED = (("data", "reid_root"), ("data", "reid_csv"),
         ("output", "project"), ("output", "name"))


def split_label_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    counts: Counter[tuple[str, int]] = Counter(
        (row["split"], int(row["label"])) for row in rows)
    return {split: {"positive": counts[(split, 1)], "negative": counts[(split, 0)]}
            for split in sorted({row["split"] for row in rows})}


def validate_pairs(path: Path, tasks: str) -> dict:
    rows = read_csv(path)
    required = {"img1", "img2", "label", "split"}
    missing = required - set(rows[0] if rows else {})
    if missing:
        raise SystemExit(f"{path} is missing columns: {sorted(missing)}")
    invalid = [row for row in rows if row.get("label") not in {"0", "1"}]
    if invalid:
        raise SystemExit(f"{path} contains {len(invalid)} unresolved/invalid labels")
    counts = split_label_counts(rows)
    if tasks in {"reid", "both"}:
        for split in ("train", "val"):
            value = counts.get(split, {"positive": 0, "negative": 0})
            if not value["positive"] or not value["negative"]:
                raise SystemExit(
                    f"split={split} needs both positive and negative pairs "
                    f"(found {value}); the trainer cannot evaluate ROC-AUC otherwise")
    return counts


def pair_provenance(path: Path) -> dict:
    """Auditable source counts without changing the trainer's compact pair schema."""
    rows = read_csv(path)
    by_split_evidence = Counter((row.get("split", ""), row.get("evidence", "unknown"))
                                for row in rows)
    return {
        "rows": len(rows),
        "human_reviewed": sum(row.get("evidence", "").startswith("reviewed_") for row in rows),
        "by_split_evidence": {
            split: dict(sorted((evidence, count) for (item_split, evidence), count
                               in by_split_evidence.items() if item_split == split))
            for split in sorted({key[0] for key in by_split_evidence})
        },
    }


def build_config(root: Path, args, project: Path) -> dict:
    """The user's own trainer config, plus the four keys only this tool knows.

    Everything else in the file is theirs and is passed through untouched: this
    is a dataset tool, and a backbone or a learning rate is not a property of a
    dataset.
    """
    config: dict = {}
    if args.base_config:
        config = yaml.safe_load(Path(args.base_config).read_text(encoding="utf-8")) or {}
        if not isinstance(config, dict):
            raise SystemExit(f"{args.base_config}: base config must be a YAML mapping")
    else:
        print("note: train.base_config is empty, so the trainer receives only the "
              "dataset and output paths and must supply its own defaults", flush=True)

    for override in args.set or []:
        section, _, rest = override.partition(".")
        key, _, value = rest.partition("=")
        if not section or not key or "=" not in rest:
            raise SystemExit(f"--set expects section.key=value, got {override!r}")
        if (section, key) in OWNED:
            raise SystemExit(
                f"{section}.{key} is derived from the project (dataset root, "
                "train.pairs, train.name) and cannot be overridden here")
        config.setdefault(section, {})[key] = yaml.safe_load(value)

    # Written last so no override can point the trainer at another dataset.
    config.setdefault("data", {}).update({"reid_root": str(root), "reid_csv": args.pairs})
    config.setdefault("output", {}).update({"project": str(project), "name": args.name})
    return config


def git_revision(path: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def latest_run(project: Path, tasks: str, name: str) -> Path | None:
    directory = project / TASK_DIRECTORY.get(tasks, tasks)
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir()
               if path.is_dir() and (path.name == name or path.name.startswith(name))]
    return max(matches, key=lambda path: path.stat().st_mtime, default=None)


def run(command: list[str], cwd: Path) -> int:
    print("running:", " ".join(command), flush=True)
    return subprocess.run(command, cwd=str(cwd), check=False).returncode


def train(root: Path, args) -> dict:
    trainer = Path(args.trainer).resolve()
    entry = trainer / "scripts" / "train.py"
    if not entry.is_file():
        raise SystemExit(f"trainer entry point not found: {entry}")
    pairs = root / args.pairs if not Path(args.pairs).is_absolute() else Path(args.pairs)
    if not pairs.is_file():
        raise SystemExit(f"pair CSV not found: {pairs}")

    reviews = [root / path for path in (args.review or [])]
    gate = conflict_engine.report(root, pairs, [path for path in reviews if path.is_file()])
    if gate["errors"] and not args.allow_conflicts:
        raise SystemExit(
            f"refusing to train: {gate['errors']} identity-logic conflicts\n"
            + conflict_engine.main_text(gate)
            + "\nresolve them in the review UI, or pass --allow-conflicts to override")
    counts = validate_pairs(pairs, args.tasks)

    workspace = root / "training" / args.name
    workspace.mkdir(parents=True, exist_ok=True)
    project = workspace / "runs"
    config = build_config(root, args, project)
    config_path = workspace / "config.yaml"
    config_path.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False),
                           encoding="utf-8")
    print(f"config: {config_path}", flush=True)

    command = [args.python, str(entry), "--config", str(config_path)]
    manifest = {
        "schema": 1, "created_at": datetime.now().astimezone().isoformat(),
        "dataset_root": str(root), "pairs": str(pairs), "pairs_sha256": sha256(pairs),
        "pair_counts": counts, "pair_provenance": pair_provenance(pairs),
        "dataset_hashes": {name: sha256(root / name) for name in
                           ("identities.csv", "tracks.csv") if (root / name).is_file()},
        "conflict_gate": {k: gate[k] for k in
                                                 ("errors", "warnings", "pending_reviews", "by_kind")},
        "trainer": str(trainer), "trainer_git": git_revision(trainer),
        "command": command, "config": config,
    }
    if args.dry_run:
        manifest["status"] = "dry-run"
        atomic_write_json(workspace / "training-manifest.json", manifest)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return manifest

    code = run(command, trainer)
    manifest["exit_code"] = code
    manifest["status"] = "trained" if code == 0 else "failed"
    run_directory = latest_run(project, args.tasks, args.name)
    if run_directory is not None:
        manifest["run_directory"] = str(run_directory)
        weights = sorted((run_directory / "weights").glob("*.pt")) \
            if (run_directory / "weights").is_dir() else []
        manifest["weights"] = {path.name: sha256(path) for path in weights}
        best = next((path for path in weights if path.name == "best_reid.pt"),
                    next((path for path in weights if path.name == "best.pt"),
                         weights[0] if weights else None))
        if code == 0 and args.export and best is not None:
            export = trainer / "scripts" / "export_onnx.py"
            export_command = [args.python, str(export), "--weights", str(best)]
            if args.tasks != "both":
                export_command += ["--tasks", args.tasks]
            manifest["export_command"] = export_command
            manifest["export_exit_code"] = run(export_command, trainer)
            onnx = best.with_suffix(".onnx")
            if onnx.is_file():
                manifest["onnx"] = {"path": str(onnx), "sha256": sha256(onnx)}
                print(f"exported: {onnx}", flush=True)
    atomic_write_json(workspace / "training-manifest.json", manifest)
    print(f"manifest: {workspace / 'training-manifest.json'}", flush=True)
    return manifest
