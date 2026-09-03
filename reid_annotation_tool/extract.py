"""Turn someone else's tracking output into a reviewable ReID dataset.

Detection, identity assignment and tracking belong to the user's pipeline
script (see contract.py). This module starts where that script stops: it walks
the recordings, hands each frame over, and turns the boxes that come back into
crops, tracks and evidence.

Evidence contract (generalised to any pipeline and any class id):

* positive  = two crops of one continuous, geometry-sane, isolated track;
* negative  = two crops of two tracks proven distinct by simultaneous
              observation with negligible overlap;
* every other cross-track relation stays unlabeled and is offered to a human.

Which of those a crop earns is decided **here**, never by the pipeline: crop
geometry, occlusion, pixel quality and pair evidence are the dataset's
contract, and they must not vary with whose tracker produced the boxes.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .contract import Observation, Pipeline, Source
from .core import PAIR_FIELDS, atomic_write_csv, atomic_write_json
from .geometry import (blur_laplacian_var, brightness_mean, contained_fraction,
                       crop_image, exposure_clip, expand_box, iou)
from .projection import BodyProjection

IDENTITY_FIELDS = ("img_path", "person_id", "split", "video", "track_id", "class_id",
                   "timestamp", "frame", "x1", "y1", "x2", "y2", "conf",
                   "crop_w", "crop_h", "blur", "brightness", "over_exposed", "under_exposed")
TRACK_FIELDS = ("person_id", "split", "video", "track_id", "class_id", "start", "end",
                "frames", "crops", "recovered", "status", "reason")
COVISIBLE_FIELDS = ("person_id1", "person_id2", "video", "split", "frames", "first_timestamp")
SLUG = re.compile(r"[^0-9a-zA-Z]+")


@dataclass(slots=True)
class CropSample:
    timestamp: float
    frame: int
    image: np.ndarray
    box: np.ndarray
    conf: float
    blur: float
    brightness: float
    over: float
    under: float
    path: str = ""


@dataclass
class TrackRecord:
    person_id: str
    split: str
    video: str
    track_id: int
    class_id: int
    start: float
    end: float
    frames: int = 0
    recovered: int = 0
    rejected: str = ""
    previous_box: np.ndarray | None = None
    samples: list[CropSample] = field(default_factory=list)


def slug(value: str, limit: int = 48) -> str:
    cleaned = SLUG.sub("-", value).strip("-").lower()
    return cleaned[:limit] or "video"


def identity_prefix(video: str, start_epoch: float | None, source: str = "") -> str:
    """Stable, collision-resistant recording prefix carrying its domain."""
    digest = hashlib.sha256((source or video).encode()).hexdigest()[:10]
    display = slug(video)
    context = f"{video} {source}"
    if start_epoch is not None:
        day = datetime.fromtimestamp(start_epoch).strftime("%Y%m%d")
    else:
        date = re.search(r"(?<!\d)(20\d{2})[-_/]?([01]\d)[-_/]?([0-3]\d)(?!\d)",
                         context)
        day = "".join(date.groups()) if date else ""
    if not day:
        return f"v{display}-{digest}"
    camera = re.search(r"(?:^|[^a-z0-9])(cam\d+)(?:[^a-z0-9]|$)",
                       context, re.IGNORECASE)
    domain = f"{camera.group(1).lower()}_d{day}" if camera else f"d{day}"
    return f"{domain}_v{display}-{digest}"


def assign_split(video: str, ratios: dict[str, float], seed: int) -> str:
    """Deterministically place a whole video in one split.

    Splitting per video (never per track or per crop) is what keeps the same
    person from appearing on both sides of a train/val boundary.
    """
    digest = hashlib.sha256(f"{seed}:{video}".encode()).hexdigest()
    position = int(digest[:16], 16) / float(1 << 64)
    total = sum(ratios.values()) or 1.0
    cursor = 0.0
    for name in ("train", "val", "test"):
        cursor += ratios.get(name, 0.0) / total
        if position < cursor:
            return name
    return "test"


def regex_target(path: Path, source: str) -> str:
    if source == "filename":
        return path.name
    if source == "path":
        return path.as_posix()
    raise ValueError(f"unsupported regex source: {source}")


def parse_start_time(path: Path, pattern: str, time_format: str,
                     source: str = "filename") -> float | None:
    if not pattern:
        return None
    target = regex_target(path, source)
    match = re.search(pattern, target)
    if not match:
        raise SystemExit(f"--timestamp-regex did not match {target}")
    return datetime.strptime("".join(match.groups()), time_format).timestamp()


def video_identifier(path: Path, pattern: str = "") -> str:
    """Stable recording id, optionally assembled from full-path regex groups."""
    if not pattern:
        return path.stem
    match = re.search(pattern, path.as_posix())
    if not match:
        raise SystemExit(f"--video-id-regex did not match {path.as_posix()}")
    values = [value for value in match.groups() if value is not None]
    if not values:
        raise SystemExit("--video-id-regex must contain at least one capture group")
    return "-".join(values)


def parse_day_splits(values: list[str] | None) -> dict[str, str]:
    """Validate an optional closed mapping from local calendar day to split."""
    result: dict[str, str] = {}
    for value in values or []:
        try:
            day, split = value.split("=", 1)
        except ValueError as error:
            raise ValueError(
                f"invalid --day-split {value!r}; expected YYYYMMDD=train|val|test"
            ) from error
        if not re.fullmatch(r"\d{8}", day) or split not in {"train", "val", "test"}:
            raise ValueError(
                f"invalid --day-split {value!r}; expected YYYYMMDD=train|val|test"
            )
        if day in result:
            raise ValueError(f"duplicate --day-split date: {day}")
        result[day] = split
    return result


def video_split(video: str, start_epoch: float | None, fixed: str | None,
                day_splits: dict[str, str], ratios: dict[str, float], seed: int) -> str:
    if fixed:
        return fixed
    if day_splits:
        if start_epoch is None:
            raise SystemExit("--day-split requires --timestamp-regex")
        day = datetime.fromtimestamp(start_epoch).strftime("%Y%m%d")
        if day not in day_splits:
            raise SystemExit(f"recording day {day} is absent from --day-split mapping")
        return day_splits[day]
    return assign_split(video, ratios, seed)


def motion_is_sane(previous: np.ndarray | None, current: np.ndarray,
                   max_shift: float, min_scale: float, max_scale: float) -> bool:
    """Reject a track whose box teleports or resizes impossibly between samples."""
    if previous is None:
        return True
    previous_center = (previous[:2] + previous[2:]) / 2.0
    current_center = (current[:2] + current[2:]) / 2.0
    scale = max(float(previous[2] - previous[0]), float(previous[3] - previous[1]), 1.0)
    previous_area = max(float(np.prod(previous[2:] - previous[:2])), 1.0)
    current_area = max(float(np.prod(current[2:] - current[:2])), 1.0)
    return (float(np.linalg.norm(current_center - previous_center)) <= max_shift * scale
            and min_scale <= current_area / previous_area <= max_scale)


def is_isolated(box: np.ndarray, others: list[np.ndarray], args) -> bool:
    """True when no other detected object materially shares this crop.

    Judged on BODY boxes, not on the detector's head boxes: the crop is a body, so
    body extent is what decides whether a second person landed in it. Two heads can
    sit well apart while one body crop swallows the other person whole.
    """
    return not any(iou(box, other) > args.max_neighbour_iou
                   or contained_fraction(box, other) > args.max_neighbour_contained
                   for other in others)


def crop_is_contaminated(found: list[Observation], class_id: int, args) -> bool:
    """True when a finished crop still contains two independent objects.

    The pipeline supplies the detections; what makes them a contamination is
    decided here, so the rule cannot drift between pipelines.
    """
    boxes = [item for item in found
             if item.class_id == class_id and item.confidence >= args.crop_conf]
    for left in range(len(boxes)):
        for right in range(left + 1, len(boxes)):
            first, second = boxes[left], boxes[right]
            if max(first.confidence, second.confidence) < args.crop_primary_conf:
                continue
            if iou(first.box, second.box) > args.max_crop_overlap:
                continue
            areas = [max(float(np.prod(item.box[2:] - item.box[:2])), 1.0)
                     for item in (first, second)]
            if min(areas) / max(areas) >= args.min_crop_relative_area:
                return True
    return False


def build_sample(frame: np.ndarray, box: np.ndarray, neighbour_boxes: list[np.ndarray],
                 timestamp: float, frame_no: int, conf: float, args) -> CropSample | None:
    """One accepted crop, or None when the crop box fails purity or pixel quality.

    ``box`` is the FINAL crop box -- projected body, margin already applied, clipped to
    the frame (see crop_boxes_of_frame). Purity must be judged on exactly the geometry
    that becomes pixels: vetting a smaller box than the one cropped would let a
    neighbour hide in the margin, and an overlapped crop poisons the ReID gallery
    irreversibly, whereas a wrongly dropped crop only costs sample count.
    """
    if not is_isolated(box, neighbour_boxes, args):
        return None
    image = crop_image(frame, box)
    if image is None or min(image.shape[:2]) < args.min_crop_side:
        return None
    blur = blur_laplacian_var(image)
    bright = brightness_mean(image)
    over, under = exposure_clip(image)
    if (blur < args.min_blur or not args.min_brightness <= bright <= args.max_brightness
            or over > args.max_exposure_clip or under > args.max_exposure_clip):
        return None
    return CropSample(timestamp, frame_no, image, box.copy(), conf,
                      blur, bright, over, under)


def crop_boxes_of_frame(boxes, frame: np.ndarray, projection: BodyProjection,
                        args) -> list[np.ndarray]:
    """Detector boxes -> the exact crop boxes their bodies would produce.

    One helper for targets and neighbours alike, so purity, co-visibility and the
    crop itself can never be judged on three slightly different rectangles.
    """
    frame_h, frame_w = frame.shape[:2]
    return [expand_box(projection.project(box, frame_w, frame_h), args.crop_margin,
                       frame_w, frame_h)
            for box in boxes]


def thin_samples(samples: list[CropSample], maximum: int) -> None:
    """Keep an even spread over the track lifetime, not the first N crops."""
    if len(samples) <= maximum:
        return
    keep = np.linspace(0, len(samples) - 1, maximum).round().astype(int)
    samples[:] = [samples[index] for index in sorted(set(keep.tolist()))]


def screen_crops(record: TrackRecord, pipeline: Pipeline, args) -> None:
    """Crop firewall, second pass: drop or reject crops that still hold two objects.

    A pipeline replaying pre-computed tracking data has no detector to re-run,
    so ``detect_crops`` is optional and its absence is not a pass — the
    manifest records that this stage was unavailable for the run.
    """
    if not record.samples or args.no_crop_firewall:
        return
    found = pipeline.detect_crops([sample.image for sample in record.samples])
    if found is None:
        return
    flags = [crop_is_contaminated(observations, record.class_id, args)
             for observations in found]
    if sum(flags) >= args.reject_track_multi_crops:
        record.rejected = "multi_object_crops"
        return
    record.samples = [sample for sample, bad in zip(record.samples, flags) if not bad]


def finalize_track(record: TrackRecord, out: Path, pipeline: Pipeline, args,
                   finished: dict[str, TrackRecord]) -> None:
    existing = finished.get(record.person_id)
    if existing is not None and existing is not record:
        raise ValueError(f"duplicate person_id would overwrite a track: {record.person_id}")
    if record.rejected:
        record.samples = []  # crops of a rejected track are never written to disk
        finished[record.person_id] = record
        return
    if record.frames < args.min_track_frames:
        record.rejected = "short_track_frames"
    elif record.end - record.start < args.min_track_sec:
        record.rejected = "short_track_seconds"
    if not record.rejected:
        screen_crops(record, pipeline, args)
    if not record.rejected and len(record.samples) < args.min_track_crops:
        record.rejected = "too_few_clean_crops"
    if record.rejected:
        record.samples = []
        finished[record.person_id] = record
        return
    thin_samples(record.samples, args.max_track_crops)
    directory = out / "images" / record.split / record.person_id
    directory.mkdir(parents=True, exist_ok=True)
    for index, sample in enumerate(record.samples):
        relative = Path("images") / record.split / record.person_id / f"{index:02d}.jpg"
        cv2.imwrite(str(out / relative), sample.image, [cv2.IMWRITE_JPEG_QUALITY, args.jpeg_quality])
        sample.path = relative.as_posix()
        sample.image = np.empty((0, 0, 3), np.uint8)
    finished[record.person_id] = record


def identity_rows(finished: dict[str, TrackRecord]) -> list[dict]:
    rows = []
    for record in finished.values():
        if record.rejected:
            continue
        for sample in record.samples:
            if not sample.path:
                continue
            rows.append({
                "img_path": sample.path, "person_id": record.person_id,
                "split": record.split, "video": record.video, "track_id": record.track_id,
                "class_id": record.class_id, "timestamp": f"{sample.timestamp:.3f}",
                "frame": sample.frame,
                "x1": f"{sample.box[0]:.1f}", "y1": f"{sample.box[1]:.1f}",
                "x2": f"{sample.box[2]:.1f}", "y2": f"{sample.box[3]:.1f}",
                "conf": f"{sample.conf:.4f}",
                "crop_w": int(round(float(sample.box[2] - sample.box[0]))),
                "crop_h": int(round(float(sample.box[3] - sample.box[1]))),
                "blur": f"{sample.blur:.2f}", "brightness": f"{sample.brightness:.2f}",
                "over_exposed": f"{sample.over:.5f}", "under_exposed": f"{sample.under:.5f}",
            })
    return sorted(rows, key=lambda row: (row["person_id"], row["img_path"]))


def stable_order(row: dict, seed: int) -> str:
    return hashlib.sha256((str(seed) + json.dumps(row, sort_keys=True)).encode()).hexdigest()


def build_pairs(finished: dict[str, TrackRecord],
                covisible: dict[tuple[str, str], dict], args) -> list[dict]:
    accepted = {key: record for key, record in finished.items()
                if not record.rejected and record.samples}
    positives: defaultdict[str, list[dict]] = defaultdict(list)
    for record in accepted.values():
        spans = []
        for left in range(len(record.samples)):
            for right in range(left + 1, len(record.samples)):
                gap = record.samples[right].timestamp - record.samples[left].timestamp
                if gap >= args.min_positive_gap:
                    spans.append((gap, left, right))
        spans.sort(reverse=True)
        for gap, left, right in spans[:args.max_positive_pairs]:
            positives[record.split].append({
                "img1": record.samples[left].path, "img2": record.samples[right].path,
                "label": 1, "split": record.split, "evidence": "same_continuous_track",
                "person_id1": record.person_id, "person_id2": record.person_id,
                "gap_sec": f"{gap:.3f}",
            })

    negatives: defaultdict[str, list[dict]] = defaultdict(list)
    for (left_key, right_key), witness in covisible.items():
        if witness["frames"] < args.min_covisible_frames:
            continue
        left, right = accepted.get(left_key), accepted.get(right_key)
        if left is None or right is None or left.split != right.split:
            continue
        for first in left.samples:
            for second in right.samples:
                negatives[left.split].append({
                    "img1": first.path, "img2": second.path, "label": 0,
                    "split": left.split, "evidence": "covisible_tracks_cross_crops",
                    "person_id1": left.person_id, "person_id2": right.person_id,
                    "gap_sec": f"{abs(first.timestamp - second.timestamp):.3f}",
                })

    rows: list[dict] = []
    for split, positive_rows in positives.items():
        chosen = sorted(negatives[split], key=lambda row: stable_order(row, args.seed))
        rows.extend(positive_rows)
        rows.extend(chosen[:round(len(positive_rows) * args.negative_ratio)])
    return sorted(rows, key=lambda row: (row["split"], stable_order(row, args.seed)))


def close_stale(records: dict[int, TrackRecord], last_seen: dict[int, int],
                processed: int, gap: int, out: Path, pipeline: Pipeline, args,
                finished: dict[str, TrackRecord]) -> None:
    """Finish tracks the pipeline has stopped reporting.

    The host decides when a track is over, not the pipeline: a track's end is
    what makes its crops a closed set of same-track evidence, and a script that
    forgot to say so must not silently produce an open-ended identity. The
    threshold counts PROCESSED frames, so it means the same thing whatever
    --frame-stride is set to.
    """
    for track_id in [key for key, seen in last_seen.items() if processed - seen > gap]:
        last_seen.pop(track_id)
        record = records.pop(track_id, None)
        if record is not None:
            finalize_track(record, out, pipeline, args, finished)


def process_video(path: Path, index: int, pipeline: Pipeline, args,
                  projection: BodyProjection, finished: dict[str, TrackRecord],
                  covisible: dict[tuple[str, str], dict]) -> dict:
    start_epoch = parse_start_time(path, args.timestamp_regex, args.timestamp_format,
                                  args.timestamp_source)
    video = video_identifier(path, args.video_id_regex)
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise SystemExit(f"cannot open video: {path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 25.0
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    origin = start_epoch if start_epoch is not None else float(index) * args.video_gap_sec
    split = video_split(video, start_epoch, args.split, args.day_splits,
                        args.split_ratios, args.seed)
    prefix = identity_prefix(video, start_epoch, path.resolve().as_posix())

    records: dict[int, TrackRecord] = {}
    last_seen: dict[int, int] = {}
    # A pipeline is free to recycle a track id once it has dropped the track.
    # Two identities must never share a person_id (they would share a crop
    # directory), so a recycled id gets a generation suffix.
    generation: Counter[int] = Counter()
    frame_no, processed = 0, 0

    pipeline.open_source(Source(video, str(path), 0, origin, width, height, fps, split))
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        number = frame_no
        frame_no += 1
        if args.max_frames and number >= args.max_frames:
            break
        if number % args.frame_stride:
            continue
        timestamp = origin + number / fps
        source = Source(video, str(path), number, timestamp, width, height, fps, split)
        found = pipeline.process_frame(frame, source)

        # Crop boxes for EVERY reported object, tracked or not: purity,
        # co-visibility and the crop itself must be judged on one rectangle,
        # and a bystander occludes just as well as a tracked object does.
        boxes = crop_boxes_of_frame([item.box for item in found], frame, projection, args)
        crop_box: dict[int, np.ndarray] = {}
        source_box: dict[int, np.ndarray] = {}
        for item, box in zip(found, boxes):
            if item.track_id is not None and item.track_id not in crop_box:
                crop_box[item.track_id] = box
                source_box[item.track_id] = item.box
        # "every box except my own", derived once per frame: getting the
        # self-exclusion wrong makes every crop look occluded by itself and
        # silently yields an empty dataset.
        others_of = {track_id: [box for item, box in zip(found, boxes)
                                if item.track_id != track_id]
                     for track_id in crop_box}

        for item in found:
            track_id = item.track_id
            if track_id is None:
                continue
            record = records.get(track_id)
            if record is None:
                generation[track_id] += 1
                suffix = "" if generation[track_id] == 1 else f"g{generation[track_id]}"
                record = TrackRecord(f"{prefix}_t{track_id:05d}{suffix}", split, video,
                                     track_id, item.class_id, timestamp, timestamp)
                records[track_id] = record
            elif processed - last_seen.get(track_id, processed) > 1:
                record.recovered += 1
            last_seen[track_id] = processed
            record.end, record.frames = timestamp, record.frames + 1
            # Motion sanity stays on the box the pipeline reported: it is the
            # measured quantity, while a projected body inherits the
            # projection's own sensitivity near the seam and would flag
            # plausible walking.
            if not motion_is_sane(record.previous_box, source_box[track_id],
                                  args.max_motion_shift, args.min_scale_ratio,
                                  args.max_scale_ratio):
                record.rejected = record.rejected or "motion_implausible"
            if not is_isolated(crop_box[track_id], others_of[track_id], args):
                record.rejected = record.rejected or "occluded_by_neighbour"
            record.previous_box = source_box[track_id].copy()

        tracked = sorted(crop_box)
        for left in range(len(tracked)):
            for right in range(left + 1, len(tracked)):
                first, second = tracked[left], tracked[right]
                if records[first].class_id != records[second].class_id:
                    continue
                # Crop boxes, not source boxes: two people standing one behind
                # the other have far-apart heads but overlapping bodies, and
                # calling that pair a certain negative would hand the trainer
                # two crops that each contain the other person.
                if iou(crop_box[first], crop_box[second]) > args.max_covisible_iou:
                    continue
                key = tuple(sorted((records[first].person_id, records[second].person_id)))
                witness = covisible.setdefault(
                    key, {"frames": 0, "timestamp": timestamp,
                          "video": video, "split": split})
                witness["frames"] += 1

        for track_id in tracked:
            record = records[track_id]
            if record.rejected:
                continue
            if (record.samples
                    and timestamp - record.samples[-1].timestamp < args.sample_interval):
                continue
            sample = build_sample(frame, crop_box[track_id], others_of[track_id],
                                  timestamp, number, float(next(
                                      item.confidence for item in found
                                      if item.track_id == track_id)), args)
            if sample is not None:
                record.samples.append(sample)
                thin_samples(record.samples, args.max_track_crops * 2)

        processed += 1
        close_stale(records, last_seen, processed, args.track_gap, args.out,
                    pipeline, args, finished)
    capture.release()
    pipeline.close_source(Source(video, str(path), frame_no, origin, width, height,
                                 fps, split))
    for record in records.values():
        finalize_track(record, args.out, pipeline, args, finished)
    accepted = sum(1 for record in finished.values()
                   if record.video == video and not record.rejected)
    print(f"{video}: split={split} frames={frame_no} processed={processed} "
          f"tracks_kept={accepted}", flush=True)
    return {"path": str(path), "video": video, "split": split, "fps": fps,
            "width": width, "height": height, "frames": frame_no,
            "processed_frames": processed, "start_epoch": start_epoch}


def extract(args) -> dict:
    """Run the whole extraction and write the dataset contract to ``args.out``."""
    videos = list(args.videos)
    if not videos:
        raise SystemExit("no videos matched")
    args.out.mkdir(parents=True, exist_ok=True)
    pipeline = Pipeline.load(args.pipeline, args.pipeline_config)
    print(f"pipeline: {pipeline.path} ({pipeline.digest[:12]})", flush=True)

    # One calibration for the whole run: it is recorded in the manifest, so every
    # crop in the dataset can be re-derived from the box columns of identities.csv.
    projection = BodyProjection.from_args(args)
    print(f"body projection: {json.dumps(projection.as_dict())}", flush=True)

    finished: dict[str, TrackRecord] = {}
    covisible: dict[tuple[str, str], dict] = {}
    manifest_videos = [process_video(path, index, pipeline, args, projection,
                                     finished, covisible)
                       for index, path in enumerate(sorted(videos))]

    identities = identity_rows(finished)
    pairs = build_pairs(finished, covisible, args)
    tracks = [{
        "person_id": record.person_id, "split": record.split, "video": record.video,
        "track_id": record.track_id, "class_id": record.class_id,
        "start": f"{record.start:.3f}", "end": f"{record.end:.3f}",
        "frames": record.frames, "crops": len(record.samples),
        "recovered": record.recovered,
        "status": "rejected" if record.rejected else "accepted",
        "reason": record.rejected,
    } for record in sorted(finished.values(), key=lambda item: item.person_id)]
    covisible_rows = [{
        "person_id1": left, "person_id2": right, "video": witness["video"],
        "split": witness["split"], "frames": witness["frames"],
        "first_timestamp": f"{witness['timestamp']:.3f}",
    } for (left, right), witness in sorted(covisible.items())]

    atomic_write_csv(args.out / "identities.csv", identities, IDENTITY_FIELDS)
    atomic_write_csv(args.out / "pairs.csv", pairs, PAIR_FIELDS)
    atomic_write_csv(args.out / "tracks.csv", tracks, TRACK_FIELDS)
    atomic_write_csv(args.out / "covisibility.csv", covisible_rows, COVISIBLE_FIELDS)

    counts = {split: {
        "identities": sum(row["split"] == split and row["status"] == "accepted" for row in tracks),
        "rejected_tracks": sum(row["split"] == split and row["status"] == "rejected" for row in tracks),
        "images": sum(row["split"] == split for row in identities),
        "positive_pairs": sum(row["split"] == split and row["label"] == 1 for row in pairs),
        "negative_pairs": sum(row["split"] == split and row["label"] == 0 for row in pairs),
    } for split in ("train", "val", "test")}
    manifest = {
        "schema": 1, "created_at": datetime.now().astimezone().isoformat(),
        "contract": {
            "positive": "two crops of one continuous isolated track",
            "negative": (f"crops of two tracks seen in >= {args.min_covisible_frames} common "
                         f"frames with IoU <= {args.max_covisible_iou}"),
            "cross_track_temporal": "unlabeled, human review only",
            "split_unit": (f"fixed:{args.split}" if args.split else
                           "calendar_day" if args.day_splits else "video"),
            # Who produced the boxes is part of what a crop means, so the
            # script and its digest are contract, not a footnote.
            "tracking": "supplied by the pipeline script, never inferred here",
            "crop_firewall": ("second detector pass" if "detect_crops" in
                              pipeline.describe()["hooks"] else
                              "unavailable: the pipeline exposes no detect_crops"),
            # Crops are perspective-projected bodies, never the raw detector box.
            # A cosine gate calibrated on one crop geometry is invalid on the other,
            # so the calibration is part of the dataset contract, not a side note.
            "crop_geometry": "nadir_projected_body",
            "body_projection": projection.as_dict(),
            "purity_judged_on": "projected body boxes",
        },
        "pipeline": pipeline.describe(),
        "videos": manifest_videos, "counts": counts,
        "arguments": {key: (sorted(str(item) for item in value) if isinstance(value, (set, frozenset))
                            else [str(item) for item in value] if isinstance(value, list)
                            else str(value) if isinstance(value, Path) else value)
                      for key, value in vars(args).items()},
    }
    atomic_write_json(args.out / "manifest.json", manifest)
    print(json.dumps(counts, ensure_ascii=False), flush=True)
    return manifest
