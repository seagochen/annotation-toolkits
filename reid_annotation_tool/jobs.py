"""One background job at a time, wrapping app.py's DISPATCH stages so the web
UI and the CLI can never drift -- a job is exactly a CLI invocation, run off
the request thread instead of blocking it.

Only one job runs at a time, by design, not as a temporary limitation:

- In-process stages (extract/mine/check/finalize/purge-domain) are captured by
  swapping `sys.stdout`, which is process-wide -- two concurrent jobs would
  interleave into each other's logs.
- extract/mine/train are already whole-machine, single-process operations;
  nothing about running two of them at once against one dataset is meaningful.

`serve` is deliberately never dispatched here: it blocks forever
(`serve_forever()`), which would permanently wedge the one worker this module
runs.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .core import atomic_write_json

# extract/mine/train are long-running or subprocess-blocking (see the stage
# table in the project plan); check/finalize/purge-domain are fast but still
# worth routing through here so the web UI has one uniform "run a stage, watch
# it finish" surface. `status` is cheap enough to stay a plain GET route
# (server.py calls it directly); `serve`/`init` are never jobs -- see above.
JOB_STAGES = ("extract", "mine", "check", "finalize", "train", "evaluate", "purge-domain")


class JobBusyError(RuntimeError):
    """Another job is still running; the CLI's answer to this is "wait"."""


@dataclass
class Job:
    id: str
    stage: str
    status: str = "queued"                      # queued | running | done | failed
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    log: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "stage": self.stage, "status": self.status,
                "created_at": self.created_at, "started_at": self.started_at,
                "finished_at": self.finished_at, "log": self.log,
                "result": self.result, "error": self.error}

    @classmethod
    def from_dict(cls, value: dict) -> "Job":
        return cls(id=value["id"], stage=value["stage"], status=value.get("status", "queued"),
                  created_at=value.get("created_at", ""), started_at=value.get("started_at"),
                  finished_at=value.get("finished_at"), log=value.get("log") or [],
                  result=value.get("result"), error=value.get("error"))


class JobRunner:
    """Runs app.py's stage functions as trackable, one-at-a-time background jobs."""

    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._threads: dict[str, threading.Thread] = {}
        self._busy = False
        self._load_existing()

    def _load_existing(self) -> None:
        """Job history survives a server restart; a job caught mid-run by one
        did not survive it, so it is relabelled rather than shown as stuck."""
        found = []
        for path in sorted(self.jobs_dir.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                job = Job.from_dict(value)
            except (OSError, json.JSONDecodeError, KeyError):
                continue
            if job.status == "running":
                job.status = "failed"
                job.error = "interrupted: server restarted while this job was running"
                job.finished_at = job.finished_at or datetime.now().astimezone().isoformat()
                self._persist(job)
            found.append(job)
        found.sort(key=lambda job: job.created_at)
        for job in found:
            self.jobs[job.id] = job
            self._order.append(job.id)

    def list(self) -> list[Job]:
        with self.lock:
            return [self.jobs[job_id] for job_id in self._order]

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def join(self, job_id: str, timeout: float | None = None) -> None:
        """Block until a job finishes. Mainly for tests; the web API polls instead."""
        thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)

    def start(self, stage: str, project, args) -> Job:
        """Run `DISPATCH[stage](project, args)` in the background.

        `args` is whatever flags that stage reads (see app.py's stage_*
        functions) -- a plain namespace built by the caller, not argparse's.
        A `sink` attribute is attached here so a subprocess stage
        (train/evaluate, via handoff.run) can stream into this job's log; see
        handoff.run's `sink` parameter.
        """
        if stage not in JOB_STAGES:
            raise ValueError(f"not a runnable job stage: {stage!r}; one of {JOB_STAGES}")
        with self.lock:
            if self._busy:
                running = next((job for job in self.jobs.values() if job.status == "running"), None)
                raise JobBusyError(
                    f"a job is already running ({running.id if running else '?'}); "
                    "wait for it to finish")
            job = Job(id=uuid.uuid4().hex[:12], stage=stage,
                     created_at=datetime.now().astimezone().isoformat())
            self.jobs[job.id] = job
            self._order.append(job.id)
            self._busy = True
        args.sink = job.log.append
        self._persist(job)
        thread = threading.Thread(target=self._run, args=(job, stage, project, args), daemon=True)
        self._threads[job.id] = thread
        thread.start()
        return job

    def _run(self, job: Job, stage: str, project, args) -> None:
        from .app import DISPATCH

        job.status = "running"
        job.started_at = datetime.now().astimezone().isoformat()
        self._persist(job)
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                exit_code = DISPATCH[stage](project, args)
            job.result = {"exit_code": exit_code}
            if exit_code:
                job.status, job.error = "failed", f"{stage} exited with code {exit_code}"
            else:
                job.status = "done"
        except SystemExit as error:
            job.status = "failed"
            job.error = str(error.code) if error.code not in (None, 0) else str(error)
        except (ValueError, AssertionError) as error:
            job.status = "failed"
            job.error = str(error)
        except Exception:  # noqa: BLE001 - a bad job must never crash the worker
            job.status = "failed"
            job.error = traceback.format_exc()
        finally:
            captured = buffer.getvalue().splitlines()
            if captured:
                job.log.extend(captured)
            job.finished_at = datetime.now().astimezone().isoformat()
            with self.lock:
                self._busy = False
            self._persist(job)

    def _persist(self, job: Job) -> None:
        atomic_write_json(self.jobs_dir / f"{job.id}.json", job.to_dict())
