from pathlib import Path
from types import SimpleNamespace

import pytest

from reid_annotation_tool.handoff import build_eval_config, evaluate

from conftest import CANDIDATE_FIELDS, candidate, pair, write_csv
from reid_annotation_tool.core import PAIR_FIELDS


def options(**overrides):
    """The whole `evaluate:` section: the handoff, and nothing about a metric."""
    values = dict(
        pairs="pairs.csv", review=[], evaluator=Path("evaluator"), python="python",
        checkpoint=Path("checkpoint.pt"), name="exp", split="test", tasks="reid",
        base_config=None, set=[], allow_conflicts=False, dry_run=True)
    values.update(overrides)
    return SimpleNamespace(**values)


def full_pairs(root, name="pairs.csv"):
    """A dataset the evaluator accepts: both labels present in the test split."""
    add_test_identities(root)
    rows = [pair("a", "a", 1), pair("b", "c", 0),
            pair("e", "e", 1, split="test"), pair("e", "f", 0, split="test")]
    write_csv(root / name, PAIR_FIELDS, rows)
    return root / name


def add_test_identities(root):
    import csv

    from conftest import IDENTITY_FIELDS, TRACK_FIELDS

    with (root / "identities.csv").open(newline="", encoding="utf-8") as handle:
        identities = list(csv.DictReader(handle))
    with (root / "tracks.csv").open(newline="", encoding="utf-8") as handle:
        tracks = list(csv.DictReader(handle))
    for index, name in enumerate(("e", "f"), start=10):
        for crop in range(2):
            image = f"images/test/{name}/{crop:02d}.jpg"
            (root / image).parent.mkdir(parents=True, exist_ok=True)
            (root / image).write_bytes(b"jpeg")
            identities.append({"img_path": image, "person_id": name, "split": "test",
                               "video": "v1.mp4", "track_id": index, "class_id": 0,
                               "timestamp": f"{index * 10 + crop:.3f}"})
        tracks.append({"person_id": name, "split": "test", "video": "v1.mp4",
                       "track_id": index, "class_id": 0, "start": f"{index * 10:.3f}",
                       "end": f"{index * 10 + 1:.3f}", "frames": 30, "crops": 2,
                       "recovered": 0, "status": "accepted", "reason": ""})
    write_csv(root / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(root / "tracks.csv", TRACK_FIELDS, tracks)


def make_checkpoint(tmp_path) -> Path:
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"not-really-a-checkpoint")
    return checkpoint


def make_evaluator(tmp_path) -> Path:
    evaluator = tmp_path / "evaluator"
    (evaluator / "scripts").mkdir(parents=True)
    (evaluator / "scripts" / "evaluate.py").write_text("", encoding="utf-8")
    return evaluator


def test_config_is_only_the_interface(tmp_path):
    """Six keys, and not one opinion about what the evaluator measures."""
    checkpoint = make_checkpoint(tmp_path)
    config = build_eval_config(tmp_path, options(), tmp_path / "runs", checkpoint)
    assert config == {
        "data": {"reid_root": str(tmp_path), "reid_csv": "pairs.csv"},
        "checkpoint": {"path": str(checkpoint)},
        "output": {"project": str(tmp_path / "runs"), "name": "exp",
                   "metrics_path": str(tmp_path / "runs" / "metrics.json")},
    }


def test_set_overrides_any_evaluator_value(tmp_path):
    checkpoint = make_checkpoint(tmp_path)
    config = build_eval_config(tmp_path, options(set=["eval.batch_size=8"]),
                               tmp_path / "runs", checkpoint)
    assert config["eval"]["batch_size"] == 8
    with pytest.raises(SystemExit, match="section.key=value"):
        build_eval_config(tmp_path, options(set=["nonsense"]), tmp_path / "runs", checkpoint)


def test_set_cannot_repoint_the_evaluator_at_another_dataset_or_checkpoint(tmp_path):
    checkpoint = make_checkpoint(tmp_path)
    for override in ("data.reid_root=/elsewhere", "data.reid_csv=pairs.csv",
                     "checkpoint.path=/elsewhere.pt",
                     "output.project=/elsewhere", "output.name=other",
                     "output.metrics_path=/elsewhere.json"):
        with pytest.raises(SystemExit, match="cannot be overridden"):
            build_eval_config(tmp_path, options(set=[override]), tmp_path / "runs", checkpoint)


def test_missing_evaluator_is_reported(dataset):
    checkpoint = make_checkpoint(dataset)
    with pytest.raises(SystemExit, match="evaluator entry point"):
        evaluate(dataset, options(evaluator=Path("/nonexistent"), checkpoint=checkpoint))


def test_missing_checkpoint_is_reported(dataset):
    evaluator = make_evaluator(dataset)
    with pytest.raises(SystemExit, match="checkpoint not found"):
        evaluate(dataset, options(evaluator=evaluator, checkpoint=Path("/nonexistent.pt")))


def test_evaluation_refuses_a_dataset_that_contradicts_itself(dataset, tmp_path):
    path = full_pairs(dataset)
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("c1", "b", "c", "same")])
    evaluator = make_evaluator(tmp_path)
    checkpoint = make_checkpoint(tmp_path)
    values = options(evaluator=evaluator, checkpoint=checkpoint, pairs=path.name,
                     review=["review.csv"])
    with pytest.raises(SystemExit, match="identity-logic conflicts"):
        evaluate(dataset, values)
    values.allow_conflicts = True
    manifest = evaluate(dataset, values)
    assert manifest["status"] == "dry-run"
    assert set(manifest["conflict_gate"]["by_kind"]) == {"direct_contradiction",
                                                         "transitive_negative"}
    assert (dataset / "evaluation" / "exp" / "config.yaml").is_file()
    assert manifest["checkpoint"] == str(checkpoint)
    assert "checkpoint_sha256" in manifest


def test_split_without_both_labels_is_rejected(dataset, tmp_path):
    add_test_identities(dataset)
    write_csv(dataset / "pairs.csv", PAIR_FIELDS,
              [pair("e", "e", 1, split="test")])
    evaluator = make_evaluator(tmp_path)
    checkpoint = make_checkpoint(tmp_path)
    with pytest.raises(SystemExit, match="both positive and negative"):
        evaluate(dataset, options(evaluator=evaluator, checkpoint=checkpoint))
