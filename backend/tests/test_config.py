"""The project file: one place for the paths, defaults in code, typos refused.

These used to be dozens of CLI flags threaded through four commands, and a
mistyped --review silently changed which answers the conflict engine could
see. The value of the file is that it cannot do that quietly.
"""

import os

import pytest

from reid_annotation_tool import config as project_config
from reid_annotation_tool.app import latest_pairs
from reid_annotation_tool.config import ConfigError
from reid_annotation_tool.core import atomic_write_json, sha256

MINIMAL = "dataset: ./ds\npipeline:\n  script: tracking_csv\n"


@pytest.fixture(autouse=True)
def no_ambient_config(monkeypatch):
    monkeypatch.delenv(project_config.CONFIG_ENV, raising=False)


def write(tmp_path, text=MINIMAL, name="reid.yaml"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path / name


def test_the_project_file_is_found_from_any_subdirectory(tmp_path):
    write(tmp_path)
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    assert project_config.find(start=deep) == tmp_path / "reid.yaml"
    with pytest.raises(ConfigError, match="no reid.yaml in"):
        project_config.find(start=tmp_path.parent / "elsewhere")


def test_paths_resolve_against_the_file_not_the_shell(tmp_path):
    """A config that means something different per working directory eventually
    writes the dataset into the wrong place."""
    project = project_config.load(write(tmp_path))
    assert project.dataset == (tmp_path / "ds").resolve()
    assert project.resolve("m.onnx") == (tmp_path / "m.onnx").resolve()
    assert project.resolve("/abs/m.onnx").as_posix() == "/abs/m.onnx"


def test_defaults_fill_in_and_sections_merge_into_one_stage_namespace(tmp_path):
    project = project_config.load(write(tmp_path, MINIMAL + "crops:\n  min_blur: 12.0\n"))
    values = project.stage("extract")
    assert values.min_blur == 12.0            # from the file
    assert values.max_track_crops == 12       # from DEFAULTS
    assert values.split_ratios == [0.7, 0.15, 0.15]   # from another section
    assert values.frame_stride == 1
    assert project.stage("mine").splits == ["train"]


def test_latest_pairs_uses_the_finalize_pointer_not_filename_order(tmp_path):
    project = project_config.load(write(tmp_path))
    project.dataset.mkdir()
    old = project.dataset / "pairs.reviewed-v9.csv"
    current = project.dataset / "pairs.reviewed-v10.csv"
    old.write_text("old\n", encoding="utf-8")
    current.write_text("current\n", encoding="utf-8")
    atomic_write_json(project.dataset / "pairs.current.json", {
        "schema": 1, "pairs": current.name, "sha256": sha256(current),
    })
    assert latest_pairs(project) == current.name


def test_a_typo_is_refused_rather_than_ignored(tmp_path):
    with pytest.raises(ConfigError, match="unknown `crops` keys"):
        project_config.load(write(tmp_path, MINIMAL + "crops:\n  min_blurr: 12\n"))
    with pytest.raises(ConfigError, match="unknown sections"):
        project_config.load(write(tmp_path, MINIMAL + "cropz:\n  a: 1\n"))
    with pytest.raises(ConfigError, match="must be a mapping"):
        project_config.load(write(tmp_path, MINIMAL + "mine: [1, 2]\n"))
    with pytest.raises(ConfigError, match="`dataset:`"):
        project_config.load(write(tmp_path, "pipeline:\n  script: x\n"))


def test_a_retired_key_says_where_it_went(tmp_path):
    """A removed key must not read like a typo: the reader needs the migration."""
    with pytest.raises(ConfigError, match="belong to your trainer"):
        project_config.load(write(tmp_path, MINIMAL + "train:\n  backbone: osnet_x0_25\n"))
    project = project_config.load(write(tmp_path))
    with pytest.raises(ConfigError, match="belong to your trainer"):
        project_config.apply_override(project.sections, "train.reid_lr=0.001")
    # A genuine typo in the same section still reads as a typo.
    with pytest.raises(ConfigError, match="unknown `train` keys"):
        project_config.load(write(tmp_path, MINIMAL + "train:\n  trainerr: ./t\n"))


def test_the_pipeline_must_be_named_because_the_tool_does_not_track(tmp_path):
    project = project_config.load(write(tmp_path, "dataset: ./ds\n"))
    with pytest.raises(ConfigError, match="does not"):
        project.pipeline()
    project = project_config.load(
        write(tmp_path, "dataset: ./ds\npipeline:\n  script: tracking_csv\n  file: t.csv\n"))
    assert project.pipeline() == ("tracking_csv", {"file": str((tmp_path / "t.csv").resolve())})


def test_user_pipeline_script_resolves_from_the_project_file(tmp_path):
    project = project_config.load(write(
        tmp_path, "dataset: ./ds\npipeline:\n  script: ./pipeline.py\n"))
    assert project.pipeline() == (str((tmp_path / "pipeline.py").resolve()), {})


def test_models_section_is_open_but_entries_are_validated_at_use(tmp_path):
    project = project_config.load(write(
        tmp_path, MINIMAL + "models:\n  detector:\n    path: ./weights.pt\n"))
    assert project.models() == {
        "detector": {"path": str((tmp_path / "weights.pt").resolve()),
                     "framework": "pytorch", "device": "cpu", "kind": ""},
    }


def test_models_entry_needs_a_path(tmp_path):
    project = project_config.load(write(
        tmp_path, MINIMAL + "models:\n  detector:\n    device: cuda:0\n"))
    with pytest.raises(ConfigError, match="`models.detector.path` is required"):
        project.models()


def test_models_entry_rejects_unknown_keys(tmp_path):
    project = project_config.load(write(
        tmp_path, MINIMAL + "models:\n  detector:\n    path: ./w.pt\n    epochs: 10\n"))
    with pytest.raises(ConfigError, match="unknown `models.detector` keys"):
        project.models()


def test_one_run_overrides_land_on_a_known_key_only(tmp_path):
    project = project_config.load(write(tmp_path))
    project_config.apply_override(project.sections, "serve.port=9000")
    assert project.stage("serve").port == 9000
    for bad, message in (("serve.portt=1", "unknown `serve` key"),
                         ("nope.port=1", "unknown section"),
                         ("serve.port", "section.key=value")):
        with pytest.raises(ConfigError, match=message):
            project_config.apply_override(project.sections, bad)


def round_file(project, relative, mtime, labels=("",)):
    path = project.dataset / "review" / relative / "candidates.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("candidate_id,review_label\n"
                    + "".join(f"c{index},{label}\n" for index, label in enumerate(labels)),
                    encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_review_rounds_are_discovered_instead_of_retyped(tmp_path):
    project = project_config.load(write(tmp_path))
    assert project.rounds() == [] and project.live_round() is None
    assert project.next_round() == "review/v1"
    for name in ("v1", "v2"):
        round_file(project, name, 1_000_000 + int(name[1]))
    assert [path.parent.name for path in project.rounds()] == ["v1", "v2"]
    assert project.next_round() == "review/v3"


def test_nested_rounds_are_found_and_the_live_one_still_has_questions(tmp_path):
    """Real datasets nest rounds by batch and name them by purpose, so alphabetical
    order says nothing about which queue is open."""
    project = project_config.load(write(tmp_path))
    nested = round_file(project, "batch-01/cross_track_review_v2", 1_000_000, ("same",))
    flat = round_file(project, "batch-02-20260828-30", 2_000_000, ("same",))
    open_now = round_file(project, "backfill-v1", 3_000_000, ("same", ""))
    assert project.rounds() == [nested, flat, open_now]
    assert project.live_round() == open_now


def test_rewriting_a_finished_round_does_not_steal_the_live_queue(tmp_path):
    """purge-domain, archiving and finalize all rewrite old rounds. Picking the
    live queue by mtime alone would hand the reviewer a finished batch and lose
    their place mid-session."""
    project = project_config.load(write(tmp_path))
    round_file(project, "batch-02", 1_000_000, ("same", "different"))   # answered
    open_now = round_file(project, "backfill-v1", 2_000_000, ("same", ""))
    assert project.live_round() == open_now
    # a purge rewrites the finished round, making it the newest file on disk
    round_file(project, "batch-02", 9_000_000, ("same",))
    assert project.live_round() == open_now
    # once every round is answered, the newest is shown rather than nothing
    round_file(project, "backfill-v1", 2_000_000, ("same", "unclear"))
    assert project.live_round().parent.name == "batch-02"


def test_the_config_can_be_named_by_directory_or_environment(tmp_path, monkeypatch):
    """Code in one checkout, config beside the dataset is the normal layout, so
    "point me at the dataset" has to work as well as "point me at the file"."""
    write(tmp_path)
    assert project_config.find(tmp_path) == tmp_path / "reid.yaml"
    monkeypatch.setenv(project_config.CONFIG_ENV, str(tmp_path))
    elsewhere = tmp_path.parent / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    assert project_config.find(start=elsewhere) == tmp_path / "reid.yaml"
    # an explicit --config still wins over the environment
    other = tmp_path / "other"
    other.mkdir()
    write(other, name="reid.yml")
    assert project_config.find(other / "reid.yml") == other / "reid.yml"


def test_a_directory_without_a_config_says_so(tmp_path):
    with pytest.raises(ConfigError, match="no reid.yaml in"):
        project_config.find(tmp_path)


def test_save_writes_a_validated_edit_and_preserves_the_raw_dataset_string(tmp_path):
    project = project_config.load(write(tmp_path))
    project.sections["crops"]["min_blur"] = 42.0
    project_config.save(project, project.sections)
    reloaded = project_config.load(project.path)
    assert reloaded.sections["crops"]["min_blur"] == 42.0
    # `dataset: ./ds` in MINIMAL is relative; save() must not silently rewrite
    # it to the resolved absolute path.
    assert "dataset: ./ds" in project.path.read_text(encoding="utf-8")


def test_save_rejects_an_invalid_edit_without_touching_the_live_file(tmp_path):
    project = project_config.load(write(tmp_path))
    original = project.path.read_text(encoding="utf-8")
    project.sections["crops"]["not_a_real_key"] = 1
    with pytest.raises(ConfigError, match="unknown `crops` keys"):
        project_config.save(project, project.sections)
    assert project.path.read_text(encoding="utf-8") == original
    assert not project.path.with_suffix(".yaml.validate.tmp").exists()
