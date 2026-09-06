"""Review web app: JSON API plus atomic CSV label persistence.

Labels are written through a temporary file, ``fsync`` and an atomic replace, so
a reviewer can reload, crash or lose the network without losing decisions. The
dataset directory is served read-only through a path-traversal guard; nothing
outside the dataset root or the packaged web app is reachable.
"""

from __future__ import annotations

import json
import mimetypes
import threading
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

from . import conflicts as conflict_engine
from . import revision
from . import config as project_config
from .core import REVIEW_LABELS, atomic_write_csv, read_csv, relation
from .domain import csv_fields
from .jobs import JOB_STAGES, JobBusyError, JobRunner
from .provenance import Provenance

WEB_ROOT = Path(__file__).resolve().parent / "web"
GALLERY_LIMIT = 8

# Similarity columns the different mining generations wrote, most recent first.
def spread(values: list, maximum: int) -> list:
    """Evenly sample a list so a gallery shows the whole track, not its start."""
    if len(values) <= maximum:
        return values
    step = (len(values) - 1) / (maximum - 1)
    return [values[round(index * step)] for index in range(maximum)]


def config_schema() -> dict:
    """A typed shape for every closed section, derived from config.py's
    DEFAULTS rather than hand-maintained -- the config form and the actual
    validation can never drift out of sync."""
    return {section: {key: {"default": default, "type": type(default).__name__}
                      for key, default in keys.items()}
           for section, keys in project_config.DEFAULTS.items()}



class Store:
    """Thread-safe view over identities, candidates and derived conflicts."""

    def __init__(self, root: Path, candidates: Path | None, base_pairs: Path,
                 reviews: list[Path]):
        self.root, self.candidates = root, candidates
        self.base_pairs = base_pairs
        self.reviews = reviews or ([candidates] if candidates else [])
        self.lock = threading.RLock()
        self._rows: list[dict] = []
        self._stamp = -1.0
        self._gallery: dict[str, list[str]] = {}
        self._identity_meta: dict[str, dict] = {}
        self._identity_stamp = -1.0
        self._conflicts: dict | None = None
        self._conflicts_stale = False
        self._conflicts_running = False
        self.provenance = Provenance(root, candidates, base_pairs, self.reviews)

    def _reload_candidates(self) -> None:
        """No candidates.csv yet is not an error -- a brand-new project starts
        the web app before `extract`+`mine` have ever run; the review tab
        just has nothing to show until they have."""
        if self.candidates is None or not self.candidates.is_file():
            self._rows, self._stamp = [], -1.0
            return
        stamp = self.candidates.stat().st_mtime_ns
        if stamp == self._stamp:
            return
        self._rows = read_csv(self.candidates)
        self._stamp = stamp

    def _reload_identities(self) -> None:
        path = self.root / "identities.csv"
        if not path.is_file():
            return
        stamp = path.stat().st_mtime_ns
        if stamp == self._identity_stamp:
            return
        gallery: dict[str, list[dict]] = {}
        for row in read_csv(path):
            gallery.setdefault(row["person_id"], []).append(row)
        self._gallery = {}
        self._identity_meta = {}
        for identity, rows in gallery.items():
            rows.sort(key=lambda row: (float(row.get("timestamp", 0) or 0), row["img_path"]))
            self._gallery[identity] = [row["img_path"] for row in rows]
            self._identity_meta[identity] = {
                "split": rows[0].get("split", ""), "video": rows[0].get("video", ""),
                "track_id": rows[0].get("track_id", ""), "crops": len(rows),
                "start": rows[0].get("timestamp", ""), "end": rows[-1].get("timestamp", ""),
                "class_id": rows[0].get("class_id", ""),
            }
        self._identity_stamp = stamp

    def rows(self) -> list[dict]:
        with self.lock:
            self._reload_candidates()
            return self._rows

    def identity(self, person_id: str) -> dict:
        with self.lock:
            self._reload_identities()
            return {"person_id": person_id,
                    "images": self._gallery.get(person_id, []),
                    "meta": self._identity_meta.get(person_id, {})}

    def decorate(self, row: dict) -> dict:
        with self.lock:
            self._reload_identities()
            left, right = row.get("person_id1", ""), row.get("person_id2", "")
            gallery1 = self._gallery.get(left, [])
            gallery2 = self._gallery.get(right, [])
            value = dict(row)
            value.setdefault("kind", "cross_track")
            value["gallery1"] = spread(gallery1, GALLERY_LIMIT)
            value["gallery2"] = spread(gallery2, GALLERY_LIMIT)
            value["meta1"] = self._identity_meta.get(left, {})
            value["meta2"] = self._identity_meta.get(right, {})
            value["img1"] = row.get("img1") or (gallery1[0] if gallery1 else "")
            value["img2"] = row.get("img2") or (gallery2[-1] if gallery2 else "")
            return value

    def set_label(self, candidate_id: str, label: str, notes: str | None) -> dict | None:
        with self.lock:
            rows = read_csv(self.candidates)
            fields = csv_fields(self.candidates)
            if notes is not None and "review_notes" not in fields:
                fields.append("review_notes")
            target = None
            for row in rows:
                if row.get("candidate_id") == candidate_id:
                    row["review_label"] = label
                    if notes is not None:
                        row["review_notes"] = notes
                    target = row
                    break
            if target is None:
                return None
            atomic_write_csv(self.candidates, rows, fields)
            self._stamp = -1.0
            self.invalidate_conflicts()
            return self.decorate(target)

    def _injection_defaults(self, left: str, right: str) -> dict:
        """Split and evidence crops for a candidate the web has to inject."""
        with self.lock:
            self._reload_identities()
            gallery1 = self._gallery.get(left, [])
            gallery2 = self._gallery.get(right, [])
            return {
                "split": self._identity_meta.get(left, {}).get("split", ""),
                # middle crops as representative evidence images for an
                # injected candidate; the miner would have picked its own
                "img1": gallery1[len(gallery1) // 2] if gallery1 else "",
                "img2": gallery2[len(gallery2) // 2] if gallery2 else "",
            }

    def revise_base(self, left: str, right: str, verdict: str, notes: str) -> dict:
        """Supersede baked reviewed_* rows for one pair from the web.

        Runs under the store lock so no label save can interleave with the
        read-modify-write of either CSV; see revision.py for the rule and the
        provenance trail it writes.
        """
        with self.lock:
            event = revision.revise(self.candidates, self.base_pairs, left, right,
                                    verdict, notes,
                                    self._injection_defaults(left, right))
            self._stamp = -1.0  # candidates.csv was rewritten behind rows()
            self.invalidate_conflicts()
            return event

    def set_relation(self, left: str, right: str, verdict: str, notes: str) -> dict:
        """Judge one relation straight from the conflict chain.

        The chain cards answer a relation, not a queue row, so this is the one
        entry point for both writes such an answer can need: baked
        ``reviewed_*`` rows standing in the way are superseded first (archived
        and logged, see revision.py), and the verdict itself is always filed
        into the live round — relabelling the pair's candidate, or injecting
        one when the pair was never queued. Physical evidence is deliberately
        left alone: a verdict contradicting it stays flagged by the conflict
        checks instead of silently deleting a machine-derived fact.
        """
        if verdict not in revision.VERDICTS:
            raise ValueError(f"unsupported verdict: {verdict}")
        with self.lock:
            key = relation(left, right)
            if any(relation(row["person_id1"], row["person_id2"]) == key
                   and row.get("evidence", "").startswith(revision.REVISABLE_PREFIX)
                   for row in (read_csv(self.base_pairs)
                               if self.base_pairs.is_file() else [])):
                return self.revise_base(left, right, verdict, notes)
            event = {"pair": list(key), "verdict": verdict, "notes": notes,
                     "removed": [],
                     **revision.file_verdict(self.candidates, key, verdict, notes,
                                             self._injection_defaults(left, right))}
            self._stamp = -1.0  # candidates.csv was rewritten behind rows()
            self.invalidate_conflicts()
            return event

    def state(self) -> dict:
        rows = self.rows()
        by_kind: dict[str, Counter] = {}
        for row in rows:
            kind = row.get("kind", "cross_track")
            by_kind.setdefault(kind, Counter())[row.get("review_label") or "pending"] += 1
        splits = sorted({row.get("split", "") for row in rows})
        labelled = sum(1 for row in rows if row.get("review_label"))
        return {
            "dataset_root": str(self.root),
            "candidates": str(self.candidates) if self.candidates else "",
            "base_pairs": str(self.base_pairs),
            "total": len(rows), "labelled": labelled, "pending": len(rows) - labelled,
            "kinds": {kind: dict(counter) for kind, counter in sorted(by_kind.items())},
            "splits": [split for split in splits if split],
            "conflicts": self.conflict_summary(),
        }

    def invalidate_conflicts(self) -> None:
        """Mark the cached report stale and refresh it off the request thread.

        Conflict detection walks the whole identity graph, so doing it inline
        would make every keystroke wait on the size of the dataset. The reviewer
        sees the updated badge on the next answer instead of the current one.
        """
        self._conflicts_stale = True
        if self._conflicts is None or self._conflicts_running:
            return
        self._conflicts_running = True
        threading.Thread(target=self._recompute_conflicts, daemon=True).start()

    def _recompute_conflicts(self) -> None:
        try:
            value = self.provenance.decorate(
                conflict_engine.report(self.root, self.base_pairs, self.reviews))
            with self.lock:
                self._conflicts = value
                self._conflicts_stale = False
        except Exception as error:  # pragma: no cover - reported through the API
            print(f"conflict detection failed: {error}", flush=True)
        finally:
            self._conflicts_running = False

    def conflict_report(self, refresh: bool = False) -> dict:
        with self.lock:
            if self._conflicts is None or refresh or self._conflicts_stale:
                self._conflicts = self.provenance.decorate(
                    conflict_engine.report(self.root, self.base_pairs, self.reviews))
                self._conflicts_stale = False
            return self._conflicts

    def conflict_summary(self) -> dict:
        if not self.base_pairs.is_file():
            return {"available": False, "errors": 0, "warnings": 0, "stale": False}
        with self.lock:
            cached = self._conflicts
            stale = self._conflicts_stale
        value = cached if cached is not None else self.conflict_report()
        return {"available": True, "errors": value["errors"],
                "warnings": value["warnings"], "by_kind": value["by_kind"],
                "stale": stale}


class ReviewHandler(BaseHTTPRequestHandler):
    store: Store
    jobs: JobRunner
    config_path: Path | None = None
    server_version = "reid-annotation/1.0"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        if not self.path.startswith("/files/"):
            super().log_message(format, *args)

    # ---- responses -------------------------------------------------------
    def send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path, cache: str) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            self.send_error(404, "not found")
            return
        kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(payload)

    def resolve(self, base: Path, relative: str) -> Path | None:
        candidate = (base / relative.lstrip("/")).resolve()
        base = base.resolve()
        if base not in candidate.parents and candidate != base:
            return None
        return candidate if candidate.is_file() else None

    # ---- config / models / jobs -------------------------------------------
    # Every call here re-reads reid.yaml fresh via project_config.load, the
    # same way every CLI invocation does -- there is no cached Project in
    # this process, so an edit takes effect on the very next request or job.

    def load_project(self):
        if self.config_path is None:
            raise project_config.ConfigError("this server was not started with a project file")
        return project_config.load(self.config_path)

    def get_config(self) -> dict:
        project = self.load_project()
        return {"dataset": str(project.dataset), "sections": project.sections,
                "open_sections": list(project_config.OPEN_SECTIONS)}

    def save_config_section(self, section: str, patch: dict) -> dict:
        project = self.load_project()
        if section not in project.sections:
            raise project_config.ConfigError(
                f"unknown section {section!r}; known: {sorted(project.sections)}")
        if not isinstance(patch, dict):
            raise project_config.ConfigError(f"{section}: expected a mapping of keys to values")
        updated = dict(project.sections)
        updated[section] = {**updated[section], **patch}
        project_config.save(project, updated)               # validates before writing
        return project_config.load(self.config_path).sections[section]

    def save_model(self, name: str, patch: dict) -> dict:
        project = self.load_project()
        if not isinstance(patch, dict):
            raise project_config.ConfigError("model entry must be a mapping of keys to values")
        models_section = {**project.sections.get("models", {}),
                          name: {**project.sections.get("models", {}).get(name, {}), **patch}}
        project_config.save(project, {**project.sections, "models": models_section})
        reloaded = project_config.load(self.config_path)
        return reloaded.models()[name]      # re-validates the merged entry (needs `path`, etc.)

    def delete_model(self, name: str) -> None:
        project = self.load_project()
        models_section = dict(project.sections.get("models", {}))
        if name not in models_section:
            raise KeyError(f"unknown model {name!r}")
        del models_section[name]
        project_config.save(project, {**project.sections, "models": models_section})

    def get_job(self, job_id: str) -> dict:
        job = self.jobs.get(job_id)
        if job is None:
            raise KeyError(f"unknown job {job_id!r}")
        return job.to_dict()

    def start_job(self, stage: str, payload: dict) -> dict:
        project = self.load_project()
        args = SimpleNamespace(dry_run=bool(payload.get("dry_run", False)),
                               apply=bool(payload.get("apply", False)),
                               strict=bool(payload.get("strict", False)),
                               json=bool(payload.get("json", False)))
        return self.jobs.start(stage, project, args).to_dict()

    # ---- routing ---------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if route in {"/", "/index.html"}:
                self.send_file(WEB_ROOT / "index.html", "no-cache")
            elif route == "/api/state":
                self.send_json(self.store.state())
            elif route == "/api/candidates":
                self.send_json(self.candidates(query))
            elif route == "/api/conflicts":
                self.send_json(self.store.conflict_report(query.get("refresh", ["0"])[0] == "1"))
            elif route.startswith("/api/identity/"):
                self.send_json(self.store.identity(route[len("/api/identity/"):]))
            elif route == "/api/config":
                self.send_json(self.get_config())
            elif route == "/api/config/schema":
                self.send_json(config_schema())
            elif route == "/api/models":
                self.send_json(self.load_project().models())
            elif route == "/api/jobs":
                self.send_json({"stages": list(JOB_STAGES),
                                "jobs": [job.to_dict() for job in self.jobs.list()]})
            elif route.startswith("/api/jobs/"):
                self.send_json(self.get_job(route[len("/api/jobs/"):]))
            elif route.startswith("/files/"):
                target = self.resolve(self.store.root, route[len("/files/"):])
                if target is None:
                    self.send_error(404, "not found")
                else:
                    self.send_file(target, "public, max-age=86400")
            else:
                target = self.resolve(WEB_ROOT, route)
                if target is None:
                    self.send_error(404, "not found")
                else:
                    self.send_file(target, "no-cache")
        except BrokenPipeError:  # pragma: no cover - client navigated away
            pass
        except KeyError as error:
            self.send_error(404, str(error))
        except (ValueError, project_config.ConfigError) as error:
            self.send_error(400, str(error))

    def candidates(self, query: dict[str, list[str]]) -> dict:
        rows = self.store.rows()
        kind = query.get("kind", [""])[0]
        split = query.get("split", [""])[0]
        status = query.get("status", [""])[0]
        search = query.get("q", [""])[0].strip().lower()
        selected = []
        for row in rows:
            label = row.get("review_label", "")
            if kind and row.get("kind", "cross_track") != kind:
                continue
            if split and row.get("split") != split:
                continue
            if status == "pending" and label:
                continue
            if status in REVIEW_LABELS and status and label != status:
                continue
            if search and search not in json.dumps(row, ensure_ascii=False).lower():
                continue
            selected.append(row)
        offset = int(query.get("offset", ["0"])[0])
        limit = min(int(query.get("limit", ["60"])[0]), 200)
        page = selected[offset:offset + limit]
        return {"total": len(selected), "offset": offset, "limit": limit,
                "rows": [self.store.decorate(row) for row in page]}

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if route == "/api/label":
                candidate_id = str(payload["candidate_id"])
                label = str(payload["review_label"])
                if label not in REVIEW_LABELS:
                    raise ValueError(f"Unsupported label: {label}")
                notes = payload.get("review_notes")
                updated = self.store.set_label(candidate_id, label,
                                               None if notes is None else str(notes))
                if updated is None:
                    self.send_error(404, f"Unknown candidate: {candidate_id}")
                    return
                self.send_json({"ok": True, "row": updated,
                                "state": self.store.state()})
            elif route == "/api/relation":
                event = self.store.set_relation(str(payload["person_id1"]),
                                                str(payload["person_id2"]),
                                                str(payload["verdict"]),
                                                str(payload.get("notes") or ""))
                self.send_json({"ok": True, "event": event,
                                "state": self.store.state()})
            elif route.startswith("/api/config/"):
                section = route[len("/api/config/"):]
                self.send_json(self.save_config_section(section, payload))
            elif route.startswith("/api/models/"):
                name = route[len("/api/models/"):]
                self.send_json(self.save_model(name, payload))
            elif route.startswith("/api/jobs/"):
                stage = route[len("/api/jobs/"):]
                self.send_json(self.start_job(stage, payload))
            else:
                self.send_error(404, "not found")
        except JobBusyError as error:
            self.send_error(409, str(error))
        except (KeyError, ValueError, json.JSONDecodeError, project_config.ConfigError) as error:
            self.send_error(400, str(error))

    def do_DELETE(self) -> None:
        route = urlparse(self.path).path
        try:
            if route.startswith("/api/models/"):
                self.delete_model(route[len("/api/models/"):])
                self.send_json({"ok": True})
            else:
                self.send_error(404, "not found")
        except (KeyError, ValueError, project_config.ConfigError) as error:
            self.send_error(400, str(error))


def serve(root: Path, candidates: Path | None, host: str, port: int,
          base_pairs: Path | None = None, reviews: list[Path] | None = None,
          config_path: Path | None = None) -> None:
    root = root.resolve()
    candidates = candidates.resolve() if candidates else None
    if candidates is not None and not candidates.is_file():
        raise SystemExit(f"candidate CSV not found: {candidates}")
    ReviewHandler.store = Store(root, candidates,
                                (base_pairs or root / "pairs.csv").resolve(),
                                [path.resolve() for path in (reviews or
                                                             ([candidates] if candidates else []))])
    ReviewHandler.config_path = config_path.resolve() if config_path else None
    ReviewHandler.jobs = JobRunner(root / ".jobs")
    server = ThreadingHTTPServer((host, port), ReviewHandler)
    print(f"Web UI:     http://{host}:{port}/", flush=True)
    print(f"Dataset:    {root}", flush=True)
    print(f"Candidates: {candidates or '(none yet -- run extract/mine first, or from the 任务 tab)'}",
         flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
