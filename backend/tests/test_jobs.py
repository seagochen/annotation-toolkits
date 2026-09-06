import json
import threading
from types import SimpleNamespace

import pytest

from reid_annotation_tool import app as app_module
from reid_annotation_tool import config as project_config
from reid_annotation_tool.jobs import Job, JobBusyError, JobRunner


def make_project(dataset):
    config_path = dataset / "reid.yaml"
    config_path.write_text("dataset: .\n", encoding="utf-8")
    return project_config.load(config_path)


def test_check_runs_as_a_job_and_captures_stdout(dataset):
    project = make_project(dataset)
    runner = JobRunner(dataset / ".jobs")
    job = runner.start("check", project, SimpleNamespace(strict=False, json=False))
    assert job.status in {"queued", "running", "done"}
    runner.join(job.id, timeout=5)
    finished = runner.get(job.id)
    assert finished.status == "done"
    assert finished.result == {"exit_code": 0}
    assert any("errors=" in line for line in finished.log)
    assert (dataset / ".jobs" / f"{job.id}.json").is_file()

    # Job history survives a fresh JobRunner instance (e.g. a server restart).
    reloaded = JobRunner(dataset / ".jobs")
    assert reloaded.get(job.id).status == "done"


def test_unknown_stage_is_rejected(tmp_path):
    runner = JobRunner(tmp_path / ".jobs")
    with pytest.raises(ValueError, match="not a runnable job stage"):
        runner.start("serve", None, SimpleNamespace())


def test_system_exit_is_a_clean_failure_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.DISPATCH, "check",
                        lambda project, args: (_ for _ in ()).throw(SystemExit("nope")))
    runner = JobRunner(tmp_path / ".jobs")
    job = runner.start("check", None, SimpleNamespace())
    runner.join(job.id, timeout=5)
    finished = runner.get(job.id)
    assert finished.status == "failed"
    assert finished.error == "nope"


def test_value_error_is_a_clean_failure_not_a_crash(tmp_path, monkeypatch):
    def boom(project, args):
        raise ValueError("bad state")
    monkeypatch.setitem(app_module.DISPATCH, "check", boom)
    runner = JobRunner(tmp_path / ".jobs")
    job = runner.start("check", None, SimpleNamespace())
    runner.join(job.id, timeout=5)
    finished = runner.get(job.id)
    assert finished.status == "failed"
    assert finished.error == "bad state"


def test_an_unexpected_exception_is_captured_with_a_traceback(tmp_path, monkeypatch):
    def boom(project, args):
        raise RuntimeError("surprise")
    monkeypatch.setitem(app_module.DISPATCH, "check", boom)
    runner = JobRunner(tmp_path / ".jobs")
    job = runner.start("check", None, SimpleNamespace())
    runner.join(job.id, timeout=5)
    finished = runner.get(job.id)
    assert finished.status == "failed"
    assert "surprise" in finished.error
    assert "Traceback" in finished.error


def test_a_nonzero_exit_code_is_a_failed_job_not_an_exception(tmp_path, monkeypatch):
    monkeypatch.setitem(app_module.DISPATCH, "check", lambda project, args: 1)
    runner = JobRunner(tmp_path / ".jobs")
    job = runner.start("check", None, SimpleNamespace())
    runner.join(job.id, timeout=5)
    finished = runner.get(job.id)
    assert finished.status == "failed"
    assert finished.result == {"exit_code": 1}


def test_only_one_job_runs_at_a_time(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(project, args):
        started.set()
        release.wait(timeout=5)
        return 0

    monkeypatch.setitem(app_module.DISPATCH, "check", slow)
    runner = JobRunner(tmp_path / ".jobs")
    job = runner.start("check", None, SimpleNamespace())
    assert started.wait(timeout=5)
    with pytest.raises(JobBusyError):
        runner.start("check", None, SimpleNamespace())
    release.set()
    runner.join(job.id, timeout=5)
    assert runner.get(job.id).status == "done"


def test_execution_gate_is_shared_across_projects(tmp_path, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(project, args):
        started.set()
        release.wait(timeout=5)
        return 0

    monkeypatch.setitem(app_module.DISPATCH, "check", slow)
    first = JobRunner(tmp_path / "one" / ".jobs")
    second = JobRunner(tmp_path / "two" / ".jobs")
    job = first.start("check", None, SimpleNamespace())
    assert started.wait(timeout=5)
    with pytest.raises(JobBusyError, match="another project action"):
        second.start("check", None, SimpleNamespace())
    release.set()
    first.join(job.id, timeout=5)
    assert first.get(job.id).status == "done"


def test_start_persistence_failure_releases_execution_gate(tmp_path, monkeypatch):
    broken = JobRunner(tmp_path / "broken" / ".jobs")
    original_persist = broken._persist

    def fail(job):
        raise OSError("disk full")

    monkeypatch.setattr(broken, "_persist", fail)
    with pytest.raises(OSError, match="disk full"):
        broken.start("check", None, SimpleNamespace())
    monkeypatch.setattr(broken, "_persist", original_persist)
    monkeypatch.setitem(app_module.DISPATCH, "check", lambda project, args: 0)

    job = broken.start("check", None, SimpleNamespace())
    broken.join(job.id, timeout=5)
    assert broken.get(job.id).status == "done"


@pytest.mark.parametrize("state", ["queued", "running"])
def test_an_unfinished_job_found_on_disk_is_marked_interrupted(tmp_path, state):
    """A job persisted mid-run did not survive whatever stopped the process
    that was running it -- it must not show as permanently "running"."""
    jobs_dir = tmp_path / ".jobs"
    jobs_dir.mkdir()
    stuck = Job(id="abc123", stage="extract", status=state, created_at="2020-01-01T00:00:00")
    (jobs_dir / "abc123.json").write_text(json.dumps(stuck.to_dict()), encoding="utf-8")
    runner = JobRunner(jobs_dir)
    reloaded = runner.get("abc123")
    assert reloaded.status == "failed"
    assert "interrupted" in reloaded.error
