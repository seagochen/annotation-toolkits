"""End to end across the new boundary: someone else's tracking data in, dataset out.

This is the case the tool is built around — the user's own system already did
detection, identity assignment and tracking, and what is missing is turning
that output into a reviewable ReID dataset. It runs with no detector, no ReID
model and no GPU, which is the point: the models are the user's, and their
absence must not stop the workbench from working.
"""

import csv

import cv2
import numpy as np
import pytest

from reid_annotation_tool import config as project_config
from reid_annotation_tool.app import main, stage_extract
from reid_annotation_tool.contract import Pipeline, PipelineError
from reid_annotation_tool.core import read_csv

FRAMES, SIZE = 90, (480, 640)


def moving_box(index: int, offset: int) -> tuple[int, int, int, int]:
    """A box that drifts across the frame so motion sanity stays happy."""
    x = 40 + offset + index * 2
    return x, 120, x + 70, 260


@pytest.fixture
def project(tmp_path):
    """A recording plus the tracking CSV a user's pipeline would have written."""
    video = tmp_path / "recording" / "cam1-0001.mp4"
    video.parent.mkdir(parents=True)
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30.0,
                             (SIZE[1], SIZE[0]))
    rows = []
    for index in range(FRAMES):
        frame = np.full((*SIZE, 3), 128, np.uint8)
        # two well-separated objects, each with its own texture so the blur
        # gate passes; they never overlap, so they are certain negatives
        for track_id, offset in ((1, 0), (2, 300)):
            x1, y1, x2, y2 = moving_box(index, offset)
            patch = np.random.default_rng(track_id * 1000 + index).integers(
                0, 255, (y2 - y1, x2 - x1, 3), dtype=np.uint8)
            frame[y1:y2, x1:x2] = patch
            rows.append({"source": "cam1-0001", "frame": index, "track_id": track_id,
                         "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                         "class_id": 0, "conf": 0.9})
        writer.write(frame)
    writer.release()

    tracks = tmp_path / "tracks.csv"
    with tracks.open("w", newline="", encoding="utf-8") as handle:
        out = csv.DictWriter(handle, fieldnames=list(rows[0]))
        out.writeheader()
        out.writerows(rows)

    (tmp_path / "reid.yaml").write_text(f"""
dataset: ./ds
pipeline:
  script: tracking_csv
  file: {tracks}
extract:
  videos_dir: ./recording
  pattern: "*.mp4"
splits:
  split: train
projection:
  extent_k: [1.0, 1.0, 1.0]
crops:
  min_blur: 5.0
  sample_interval: 0.3
  min_track_sec: 1.0
pairs:
  min_covisible_frames: 5
""", encoding="utf-8")
    return project_config.load(tmp_path / "reid.yaml")


def test_tracking_data_becomes_a_reviewable_dataset(project):
    stage_extract(project, None)
    root = project.dataset

    tracks = read_csv(root / "tracks.csv")
    assert {row["status"] for row in tracks} == {"accepted"}
    assert len(tracks) == 2                      # one identity per track id
    assert {row["video"] for row in tracks} == {"cam1-0001"}

    identities = read_csv(root / "identities.csv")
    assert len(identities) >= 8
    assert {row["split"] for row in identities} == {"train"}
    for row in identities:
        assert (root / row["img_path"]).is_file()

    pairs = read_csv(root / "pairs.csv")
    kinds = {row["evidence"] for row in pairs}
    # positives from one continuous track, negatives from simultaneous
    # observation — exactly the two the extractor is allowed to assert
    assert kinds == {"same_continuous_track", "covisible_tracks_cross_crops"}
    for row in pairs:
        if row["evidence"] == "same_continuous_track":
            assert row["person_id1"] == row["person_id2"]
        else:
            assert row["person_id1"] != row["person_id2"]

    covisible = read_csv(root / "covisibility.csv")
    assert len(covisible) == 1 and int(covisible[0]["frames"]) > 5


def test_the_manifest_pins_the_script_that_produced_the_boxes(project):
    stage_extract(project, None)
    import json

    manifest = json.loads((project.dataset / "manifest.json").read_text())
    pipeline = manifest["pipeline"]
    assert pipeline["script"].endswith("tracking_csv.py")
    assert len(pipeline["sha256"]) == 64
    # a replay pipeline has no detector to re-run, and the manifest says so
    # instead of letting the crops look as if they passed the firewall
    assert pipeline["hooks"] == []
    assert "unavailable" in manifest["contract"]["crop_firewall"]


def test_untracked_boxes_veto_crops_without_the_tool_owning_a_detector(project, tmp_path):
    """A bystander reported without a track id still occludes.

    This is what lets the purity rules work while the tool owns no detector:
    the pipeline reports everything it saw, and only the boxes carrying a track
    id become identities.
    """
    script = tmp_path / "occluder.py"
    script.write_text(
        "from reid_annotation_tool.contract import Observation\n"
        "\n"
        "def process_frame(image, source, config):\n"
        "    x = 40 + source.index * 2\n"
        "    return [Observation([x, 120, x + 70, 260], track_id=1),\n"
        "            Observation([x + 10, 120, x + 80, 260])]\n", encoding="utf-8")
    project_config.apply_override(project.sections, f"pipeline.script={script}")
    stage_extract(project, None)
    tracks = read_csv(project.dataset / "tracks.csv")
    assert [row["status"] for row in tracks] == ["rejected"]
    assert [row["reason"] for row in tracks] == ["occluded_by_neighbour"]


def test_a_script_without_process_frame_is_refused_by_name(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text("VALUE = 1\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="defines no process_frame"):
        Pipeline.load(str(script))


def test_a_script_returning_the_wrong_type_is_refused_at_the_boundary(tmp_path):
    script = tmp_path / "wrong.py"
    script.write_text("def process_frame(image, source, config):\n"
                      "    return [{'box': [0, 0, 1, 1]}]\n", encoding="utf-8")
    pipeline = Pipeline.load(str(script))
    with pytest.raises(PipelineError, match="expected .*Observation"):
        pipeline.process_frame(None, None)


def test_status_reports_the_next_step_without_any_flags(project, capsys):
    stage_extract(project, None)
    assert main(["--config", str(project.path)]) == 0
    printed = capsys.readouterr().out
    assert str(project.dataset) in printed
    assert "python app.py mine" in printed      # crops exist, no review round yet


def test_legacy_serve_command_is_removed():
    with pytest.raises(SystemExit) as exit_info:
        main(["serve"])
    assert exit_info.value.code == 2
