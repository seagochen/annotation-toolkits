import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from reid_annotation_tool.handoff import (build_config, latest_run, pair_provenance,
                                          run, train, validate_pairs)

from conftest import CANDIDATE_FIELDS, candidate, pair, write_csv
from reid_annotation_tool.core import PAIR_FIELDS


def options(**overrides):
    """The whole `train:` section: the handoff, and nothing about a model."""
    values = dict(
        pairs="pairs.csv", review=[], trainer=Path("trainer"), python="python",
        name="exp", tasks="reid", base_config=None, set=[], export=False,
        allow_conflicts=False, dry_run=True)
    values.update(overrides)
    return SimpleNamespace(**values)


def full_pairs(root, name="pairs.csv"):
    """A dataset the trainer accepts: both labels present in train and val."""
    add_val_identities(root)
    rows = [pair("a", "a", 1), pair("b", "c", 0),
            pair("e", "e", 1, split="val"), pair("e", "f", 0, split="val")]
    write_csv(root / name, PAIR_FIELDS, rows)
    return root / name


def add_val_identities(root):
    import csv

    from conftest import IDENTITY_FIELDS, TRACK_FIELDS

    with (root / "identities.csv").open(newline="", encoding="utf-8") as handle:
        identities = list(csv.DictReader(handle))
    with (root / "tracks.csv").open(newline="", encoding="utf-8") as handle:
        tracks = list(csv.DictReader(handle))
    for index, name in enumerate(("e", "f"), start=10):
        for crop in range(2):
            image = f"images/val/{name}/{crop:02d}.jpg"
            (root / image).parent.mkdir(parents=True, exist_ok=True)
            (root / image).write_bytes(b"jpeg")
            identities.append({"img_path": image, "person_id": name, "split": "val",
                               "video": "v1.mp4", "track_id": index, "class_id": 0,
                               "timestamp": f"{index * 10 + crop:.3f}"})
        tracks.append({"person_id": name, "split": "val", "video": "v1.mp4",
                       "track_id": index, "class_id": 0, "start": f"{index * 10:.3f}",
                       "end": f"{index * 10 + 1:.3f}", "frames": 30, "crops": 2,
                       "recovered": 0, "status": "accepted", "reason": ""})
    write_csv(root / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(root / "tracks.csv", TRACK_FIELDS, tracks)


def test_split_counts_and_validation(dataset):
    path = full_pairs(dataset)
    assert validate_pairs(path, "reid")["val"] == {"positive": 1, "negative": 1}
    write_csv(path, PAIR_FIELDS, [pair("a", "a", 1), pair("e", "e", 1, split="val")])
    with pytest.raises(SystemExit, match="both positive and negative"):
        validate_pairs(path, "reid")
    with pytest.raises(SystemExit, match="both positive and negative"):
        validate_pairs(path, "both")


def test_missing_columns_are_reported(tmp_path):
    (tmp_path / "bad.csv").write_text("img1,img2\na.jpg,b.jpg\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="missing columns"):
        validate_pairs(tmp_path / "bad.csv", "reid")


def test_unresolved_labels_are_rejected(tmp_path):
    path = tmp_path / "pairs.csv"
    write_csv(path, PAIR_FIELDS, [pair("a", "b", "")])
    with pytest.raises(SystemExit, match="unresolved/invalid"):
        validate_pairs(path, "reid")


def test_pair_provenance_counts_human_evidence(tmp_path):
    path = tmp_path / "pairs.csv"
    write_csv(path, PAIR_FIELDS, [pair("a", "b", 0, evidence="reviewed_model_mined_different"),
                                  pair("c", "d", 0, evidence="covisible_tracks_cross_crops")])
    report = pair_provenance(path)
    assert report["rows"] == 2 and report["human_reviewed"] == 1


def test_config_is_only_the_interface(tmp_path):
    """Four keys, and not one opinion about somebody else's model."""
    config = build_config(tmp_path, options(), tmp_path / "runs")
    assert config == {"data": {"reid_root": str(tmp_path), "reid_csv": "pairs.csv"},
                      "output": {"project": str(tmp_path / "runs"), "name": "exp"}}


def test_set_overrides_any_trainer_value(tmp_path):
    config = build_config(tmp_path, options(set=["train.batch_size=8", "advanced.device=cpu"]),
                          tmp_path / "runs")
    assert config["train"]["batch_size"] == 8 and config["advanced"]["device"] == "cpu"
    with pytest.raises(SystemExit, match="section.key=value"):
        build_config(tmp_path, options(set=["nonsense"]), tmp_path / "runs")


def test_set_cannot_repoint_the_trainer_at_another_dataset(tmp_path):
    """The provenance chain is worthless if an override can silently break it."""
    for override in ("data.reid_root=/elsewhere", "data.reid_csv=pairs.csv",
                     "output.project=/elsewhere", "output.name=other"):
        with pytest.raises(SystemExit, match="cannot be overridden"):
            build_config(tmp_path, options(set=[override]), tmp_path / "runs")


def test_base_config_must_be_a_mapping(tmp_path):
    (tmp_path / "base.yaml").write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="must be a YAML mapping"):
        build_config(tmp_path, options(base_config=tmp_path / "base.yaml"),
                     tmp_path / "runs")


def test_base_config_is_passed_through_untouched(tmp_path):
    """The trainer's own hyperparameters survive verbatim; only paths are added."""
    (tmp_path / "base.yaml").write_text(
        "model:\n  backbone: osnet_x0_25\ntrain:\n  reid_lr: 0.0001\n"
        "  triplet_margin: 0.9\nadvanced:\n  ema: true\n", encoding="utf-8")
    config = build_config(tmp_path, options(base_config=tmp_path / "base.yaml"),
                          tmp_path / "runs")
    assert config["model"] == {"backbone": "osnet_x0_25"}
    assert config["train"] == {"reid_lr": 0.0001, "triplet_margin": 0.9}
    assert config["advanced"] == {"ema": True}
    assert config["data"]["reid_csv"] == "pairs.csv"


def test_latest_run_finds_the_incremented_directory(tmp_path):
    for name in ("exp", "exp2"):
        (tmp_path / "reid" / name).mkdir(parents=True)
    assert latest_run(tmp_path, "reid", "exp").name in {"exp", "exp2"}
    assert latest_run(tmp_path, "cls", "exp") is None


def test_training_refuses_a_dataset_that_contradicts_itself(dataset, tmp_path):
    path = full_pairs(dataset)
    write_csv(dataset / "review.csv", CANDIDATE_FIELDS,
              [candidate("c1", "b", "c", "same")])
    trainer = tmp_path / "trainer"
    (trainer / "scripts").mkdir(parents=True)
    (trainer / "scripts" / "train.py").write_text("", encoding="utf-8")
    values = options(trainer=trainer, pairs=path.name, review=["review.csv"])
    with pytest.raises(SystemExit, match="identity-logic conflicts"):
        train(dataset, values)
    values.allow_conflicts = True
    manifest = train(dataset, values)
    assert manifest["status"] == "dry-run"
    assert set(manifest["conflict_gate"]["by_kind"]) == {"direct_contradiction",
                                                         "transitive_negative"}
    assert (dataset / "training" / "exp" / "config.yaml").is_file()


def test_missing_trainer_is_reported(dataset):
    with pytest.raises(SystemExit, match="trainer entry point"):
        train(dataset, options(trainer=Path("/nonexistent")))


def test_run_without_a_sink_behaves_exactly_as_before(tmp_path):
    assert run([sys.executable, "-c", "print('hi')"], tmp_path) == 0


def test_run_with_a_sink_streams_combined_output_line_by_line(tmp_path):
    lines = []
    code = run([sys.executable, "-c",
               "import sys; print('out', flush=True); "
               "print('err', file=sys.stderr, flush=True)"],
              tmp_path, sink=lines.append)
    assert code == 0
    assert lines == ["out", "err"]
