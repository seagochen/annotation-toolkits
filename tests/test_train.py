from pathlib import Path
from types import SimpleNamespace

import pytest

from reid_annotation_tool.train import (build_config, latest_run, train,
                                        validate_pairs)

from conftest import CANDIDATE_FIELDS, candidate, pair, write_csv
from reid_annotation_tool.core import PAIR_FIELDS


def options(**overrides):
    values = dict(
        pairs="pairs.csv", review=[], trainer=Path("trainer"), python="python",
        name="exp", base_config=None, backbone="osnet_x0_25", pretrained_path="",
        no_pretrained=False, tasks="reid", objective="id_triplet", reid_dim=512,
        num_classes=4, img_size=224, reid_epochs=10, cls_epochs=0, reid_lr=1e-4,
        cls_lr=1e-4, backbone_lr=1e-5, batch_size=32, workers=4, patience=15,
        data_cls="", csv_cls="labels.csv", device="", seed=0, set=[], export=False,
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


def test_config_points_the_trainer_at_the_reviewed_dataset(tmp_path):
    config = build_config(tmp_path, options(), tmp_path / "runs")
    assert config["data"] == {"cls_root": "", "cls_csv": "labels.csv",
                              "reid_root": str(tmp_path), "reid_csv": "pairs.csv"}
    assert config["model"]["backbone"] == "osnet_x0_25"
    assert config["train"]["identities_per_batch"] == 8
    assert config["output"]["project"] == str(tmp_path / "runs")


def test_contrastive_objective_skips_pk_sampler_defaults(tmp_path):
    config = build_config(tmp_path, options(objective="contrastive"), tmp_path / "runs")
    assert "identities_per_batch" not in config["train"]


def test_set_overrides_any_trainer_value(tmp_path):
    config = build_config(tmp_path, options(set=["train.batch_size=8", "advanced.device=cpu"]),
                          tmp_path / "runs")
    assert config["train"]["batch_size"] == 8 and config["advanced"]["device"] == "cpu"
    with pytest.raises(SystemExit, match="section.key=value"):
        build_config(tmp_path, options(set=["nonsense"]), tmp_path / "runs")


def test_base_config_is_extended_not_replaced(tmp_path):
    (tmp_path / "base.yaml").write_text(
        "train:\n  triplet_margin: 0.9\nadvanced:\n  ema: true\n", encoding="utf-8")
    config = build_config(tmp_path, options(base_config=tmp_path / "base.yaml"),
                          tmp_path / "runs")
    assert config["train"]["triplet_margin"] == 0.9 and config["advanced"]["ema"] is True


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
