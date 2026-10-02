"""The one set of atomic writers and path locks, as seen from both packages."""

from pathlib import Path
from types import SimpleNamespace

import pytest

import local_files
from annotation_platform.image_dataset import file_lock as platform_lock
from reid_annotation_tool import app as app_module
from reid_annotation_tool import core
from reid_annotation_tool.jobs import JobRunner
from reid_annotation_tool.review_store import Store

from conftest import CANDIDATE_FIELDS, candidate, write_csv


@pytest.fixture
def failing_replace(monkeypatch):
    """Make the final rename fail, as a full disk or a permission error would."""

    def fail(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail)


def test_reid_core_re_exports_the_shared_writers():
    assert core.atomic_write_csv is local_files.atomic_write_csv
    assert core.atomic_write_json is local_files.atomic_write_json
    assert core.atomic_write_text is local_files.atomic_write_text


@pytest.mark.parametrize("write", [
    lambda path: local_files.atomic_write_csv(path, ("a",), [{"a": "new"}]),
    lambda path: local_files.atomic_write_json(path, {"a": "new"}),
    lambda path: local_files.atomic_write_text(path, "new"),
    lambda path: local_files.atomic_write_bytes(path, b"new"),
])
def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary(
        tmp_path, failing_replace, write):
    target = tmp_path / "out.dat"
    target.write_text("old", encoding="utf-8")
    with pytest.raises(OSError, match="replace failed"):
        write(target)
    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_review_label_save_leaves_no_temporary(dataset, failing_replace):
    review = dataset / "review.csv"
    write_csv(review, CANDIDATE_FIELDS, [candidate("c1", "a", "b")])
    before = review.read_text(encoding="utf-8")
    with pytest.raises(OSError, match="replace failed"):
        Store(dataset, review).set_label("c1", "same", None)
    assert review.read_text(encoding="utf-8") == before
    assert not list(dataset.glob("*.tmp"))


def test_failed_job_record_save_leaves_no_temporary(tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.DISPATCH, "check", lambda project, args: 0)
    runner = JobRunner(tmp_path / ".jobs")
    job = runner.start("check", None, SimpleNamespace())
    runner.join(job.id, timeout=5)
    record = tmp_path / ".jobs" / f"{job.id}.json"
    before = record.read_text(encoding="utf-8")

    def fail(self, target):
        raise OSError("replace failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        runner._persist(runner.get(job.id))
    assert record.read_text(encoding="utf-8") == before
    assert not list((tmp_path / ".jobs").glob("*.tmp"))


def test_review_store_and_platform_modules_share_one_lock_per_path(dataset):
    review = dataset / "review.csv"
    write_csv(review, CANDIDATE_FIELDS, [candidate("c1", "a", "b")])
    store = Store(dataset, review)
    assert store.lock is platform_lock(review)
    assert store.lock is local_files.file_lock(dataset / "." / "review.csv")
