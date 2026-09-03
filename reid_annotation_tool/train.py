"""Retrain the ReID model from a reviewed dataset with an external trainer plugin.

The bridge is deliberately narrow: this tool owns the *evidence* (which pairs
are trustworthy and why), the plugin owns the *optimisation*. Training refuses
to start on a dataset whose identity logic contradicts itself, and every run
records the exact CSV digest it consumed so a checkpoint can be traced back to
the annotations that produced it.
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
    counts = split_label_counts(rows)
    if tasks in {"reid", "both"}:
        for split in ("train", "val"):
            value = counts.get(split, {"positive": 0, "negative": 0})
            if not value["positive"] or not value["negative"]:
                raise SystemExit(
                    f"split={split} needs both positive and negative pairs "
                    f"(found {value}); the trainer cannot evaluate ROC-AUC otherwise")
    return counts


def build_config(root: Path, args, project: Path) -> dict:
    config: dict = {}
    if args.base_config:
        config = yaml.safe_load(Path(args.base_config).read_text(encoding="utf-8")) or {}
    for section in ("model", "data", "train", "val", "output", "advanced"):
        config.setdefault(section, {})
    config["model"].update({
        "backbone": args.backbone, "pretrained": not args.no_pretrained,
        "tasks": args.tasks, "reid_dim": args.reid_dim,
        "input_size": [args.img_size, args.img_size],
    })
    config["model"].setdefault("num_classes", args.num_classes)
    if args.pretrained_path:
        config["model"]["pretrained_path"] = str(Path(args.pretrained_path).resolve())
    config["data"].update({
        "cls_root": str(Path(args.data_cls).resolve()) if args.data_cls else "",
        "cls_csv": args.csv_cls, "reid_root": str(root), "reid_csv": args.pairs,
    })
    config["train"].update({
        "reid_objective": args.objective,
        "reid_epochs": args.reid_epochs, "cls_epochs": args.cls_epochs,
        "reid_lr": args.reid_lr, "cls_lr": args.cls_lr, "backbone_lr": args.backbone_lr,
        "batch_size": args.batch_size, "num_workers": args.workers,
        "patience": args.patience,
    })
    config["train"].setdefault("weight_decay", 0.0)
    config["train"].setdefault("contrastive_margin", 0.5)
    if args.objective == "id_triplet":
        config["train"].setdefault("triplet_margin", 0.3)
        config["train"].setdefault("triplet_weight", 1.0)
        config["train"].setdefault("identity_weight", 1.0)
        config["train"].setdefault("identity_label_smoothing", 0.1)
        config["train"].setdefault("identities_per_batch", 8)
        config["train"].setdefault("images_per_identity", 4)
        config["train"].setdefault("cross_track_fraction", 0.5)
        config["train"].setdefault("hard_negative_fraction", 0.5)
        config["train"].setdefault("batches_per_epoch", 0)
    config["val"].setdefault("val_split", 0.2)
    config["val"].setdefault("batch_size", 64)
    config["output"].update({"project": str(project), "name": args.name})
    config["advanced"].setdefault("ema", False)
    config["advanced"].setdefault("seed", args.seed)
    config["advanced"].setdefault("device", args.device)
    for override in args.set or []:
        section, _, rest = override.partition(".")
        key, _, value = rest.partition("=")
        if not section or not key:
            raise SystemExit(f"--set expects section.key=value, got {override!r}")
        config.setdefault(section, {})[key] = yaml.safe_load(value)
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
        "pair_counts": counts, "conflict_gate": {k: gate[k] for k in
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
