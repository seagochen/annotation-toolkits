"""Conflict-detail expansion: edge order, per-row provenance, editability,
metric comparability and cache decoration.

The fixture mirrors the real failure shape this feature was built for: a
13-node chain whose 9 middle edges are baked into pairs.csv under ONE shared
evidence name, whose 3 outer edges are live candidates, and whose two ends are
proven different by covisibility rows plus a temporal overlap.
"""

import json
import os

import pytest

from reid_annotation_tool.core import PAIR_FIELDS, read_csv
from reid_annotation_tool.provenance import (candidate_metric, edge_decision,
                                             weakest_edge_index)
from reid_annotation_tool.server import Store

from conftest import (CANDIDATE_FIELDS, COVISIBLE_FIELDS, IDENTITY_FIELDS,
                      TRACK_FIELDS, write_csv)

NODES = [f"n{index:02d}" for index in range(13)]
CHAIN_EVIDENCE = "reviewed_model_mined_same"
PAIR_FIELDS_WITH_BATCH = PAIR_FIELDS + ("batch",)


def chain_pair(left, right, label, evidence, split="train"):
    return {"img1": f"images/{split}/{left}/00.jpg", "img2": f"images/{split}/{right}/00.jpg",
            "label": label, "split": split, "evidence": evidence,
            "person_id1": left, "person_id2": right, "gap_sec": "1", "batch": "batch-t"}


def chain_candidate(candidate_id, left, right, label, cosine, split="train"):
    return {"candidate_id": candidate_id, "kind": "cross_track", "split": split,
            "person_id1": left, "person_id2": right,
            "img1": f"images/{split}/{left}/00.jpg", "img2": f"images/{split}/{right}/00.jpg",
            "time_gap_sec": "10", "cosine": cosine, "rank_score": cosine,
            "review_label": label, "review_notes": ""}


@pytest.fixture
def chain_dataset(tmp_path):
    """13 identities, 12 same-edges (9 base + 3 live candidates), contradicted ends."""
    identities, tracks = [], []
    # x01/x02 exist only to carry a decoy base edge under the SAME evidence
    # name as the chain edges; it must never leak into the chain expansion.
    for index, name in enumerate(NODES + ["x01", "x02"]):
        # endpoints share one video with a 3-second overlap; everyone else
        # lives in a private video so no other temporal conflict fires
        if name == "n00":
            video, start, end = "v_overlap.mp4", 100.0, 110.0
        elif name == "n12":
            video, start, end = "v_overlap.mp4", 105.0, 108.0
        else:
            video, start, end = f"v_{name}.mp4", 1000.0 * index, 1000.0 * index + 5
        for crop in range(2):
            path = f"images/train/{name}/{crop:02d}.jpg"
            (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / path).write_bytes(b"jpeg")
            identities.append({"img_path": path, "person_id": name, "split": "train",
                               "video": video, "track_id": index, "class_id": 0,
                               "timestamp": f"{start + crop:.3f}"})
        tracks.append({"person_id": name, "split": "train", "video": video,
                       "track_id": index, "class_id": 0, "start": f"{start:.3f}",
                       "end": f"{end:.3f}", "frames": 30, "crops": 2,
                       "recovered": 0, "status": "accepted", "reason": ""})
    write_csv(tmp_path / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(tmp_path / "tracks.csv", TRACK_FIELDS, tracks)
    write_csv(tmp_path / "covisibility.csv", COVISIBLE_FIELDS,
              [{"person_id1": "n00", "person_id2": "n12", "video": "v_overlap.mp4",
                "split": "train", "frames": 7, "first_timestamp": "105.0"}])

    pairs = [chain_pair("x01", "x02", 1, CHAIN_EVIDENCE)]           # line 2: decoy
    pairs += [chain_pair(NODES[index], NODES[index + 1], 1, CHAIN_EVIDENCE)
              for index in range(2, 11)]                            # lines 3..11
    pairs += [chain_pair("n00", "n12", 0, "covisible_tracks_cross_crops"),
              chain_pair("n00", "n12", 0, "covisible_proven_different")]  # lines 12, 13
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS_WITH_BATCH, pairs)
    # base line numbers, keyed by chain edge index (edge i joins path[i]/path[i+1])
    base_lines = {index: index + 1 for index in range(2, 11)}

    # live queue: the 3 outer edges plus one pending row
    # two model files with IDENTICAL content under different paths: "same
    # model" must be decided by content hash, not by the recorded path string
    models = tmp_path / "models"
    models.mkdir()
    (models / "frozen.onnx").write_bytes(b"weights-v1")
    (models / "frozen_copy.onnx").write_bytes(b"weights-v1")
    (models / "other.onnx").write_bytes(b"weights-v2")

    current = tmp_path / "review" / "current" / "candidates.csv"
    write_csv(current, CANDIDATE_FIELDS, [
        chain_candidate("c_cur_00", "n00", "n01", "same", "0.62"),
        chain_candidate("c_cur_01", "n01", "n02", "same", "0.71"),
        chain_candidate("c_cur_11", "n11", "n12", "same", "0.55"),
        chain_candidate("c_cur_pending", "x01", "n01", "", "0.99"),
    ])
    (current.parent / "report.json").write_text(
        json.dumps({"model": str(models / "frozen.onnx")}))
    # NOTE: no candidate_source.csv here on purpose — degradation path.

    # historical round: answered the 9 base pairs; back-references + metrics.
    # h_neg is a provenance-only "different" answer with an extreme metric: it
    # must never participate in weakest-edge ranking (it does not support the
    # same-edge), and it never reaches the conflict engine (only Store.reviews
    # does).
    hist = tmp_path / "review" / "hist" / "candidates.csv"
    write_csv(hist, CANDIDATE_FIELDS, [
        chain_candidate(f"h_{index:02d}", NODES[index], NODES[index + 1], "same",
                        f"{0.90 - 0.01 * index:.2f}")
        for index in range(2, 11)
    ] + [chain_candidate("h_neg", "n02", "n03", "different", "0.01")])
    (hist.parent / "mine.report.json").write_text(
        json.dumps({"model": str(models / "frozen_copy.onnx")}))

    # a review file OUTSIDE <root>/review/**: only reachable through
    # Store.reviews, like an arbitrary --review path on the CLI
    extra = tmp_path / "extra" / "answers.csv"
    write_csv(extra, CANDIDATE_FIELDS,
              [chain_candidate("x_extra_05", "n05", "n06", "same", "0.50")])
    return tmp_path, current, base_lines


def make_store(chain_dataset):
    root, current, _ = chain_dataset
    return Store(root, current, root / "pairs.csv", [current])


def transitive_conflict(report):
    found = [item for item in report["conflicts"] if item["kind"] == "transitive_negative"]
    assert len(found) == 1
    return found[0]


def test_edges_follow_same_path_order(chain_dataset):
    store = make_store(chain_dataset)
    conflict = transitive_conflict(store.conflict_report())
    path = conflict["detail"]["same_path"]
    detail = conflict["conflict_detail"]
    assert len(path) == 13
    assert [(edge["left"], edge["right"]) for edge in detail["edges"]] \
        == list(zip(path, path[1:]))
    assert detail["endpoints"] == [path[0], path[-1]]
    assert detail["path"] == path


def test_grouped_transitive_paths_share_one_deduplicated_detail(chain_dataset):
    root, current, _ = chain_dataset
    rows = read_csv(root / "pairs.csv")
    rows.append(chain_pair("n00", "n10", 0, "second_proven_negative"))
    write_csv(root / "pairs.csv", PAIR_FIELDS_WITH_BATCH, rows)

    conflict = transitive_conflict(make_store(chain_dataset).conflict_report())
    detail = conflict["conflict_detail"]

    assert conflict["detail"]["negative_constraints"] == 2
    assert detail["endpoint_pairs"] == [["n00", "n10"], ["n00", "n12"]]
    assert len(detail["contradictions"]) == 2
    assert len(detail["edges"]) == 12
    assert len({tuple(sorted((edge["left"], edge["right"])))
                for edge in detail["edges"]}) == 12


def test_shared_evidence_base_edges_stay_distinct(chain_dataset):
    root, current, base_lines = chain_dataset
    store = make_store(chain_dataset)
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    path = detail["path"]
    seen_lines = set()
    for index, edge in enumerate(detail["edges"]):
        positives = [row for row in edge["base_rows"] if row["label"] == 1]
        if (edge["left"], edge["right"]) in [(NODES[i], NODES[i + 1]) for i in range(2, 11)]:
            # exactly the one pairs.csv row for THIS pair, despite 10 rows
            # (9 chain + 1 decoy) sharing the same evidence name
            assert len(positives) == 1
            expected = base_lines[path.index(edge["left"])]
            assert positives[0]["line"] == expected
            assert positives[0]["evidence"] == CHAIN_EVIDENCE
            assert positives[0]["batch"] == "batch-t"
            seen_lines.add(positives[0]["line"])
        else:
            assert positives == []
    assert len(seen_lines) == 9          # nine distinct rows, none merged
    assert 2 not in seen_lines           # the decoy row never entered the chain


def test_edges_back_reference_candidates_and_editability(chain_dataset):
    store = make_store(chain_dataset)
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    base_edge = next(edge for edge in detail["edges"]
                     if (edge["left"], edge["right"]) == ("n02", "n03"))
    answer = next(a for a in base_edge["answers"] if a["candidate_id"] == "h_02")
    assert answer["file"] == "review/hist/candidates.csv"
    assert answer["editable"] is False   # historical round: locate only
    live_edge = next(edge for edge in detail["edges"]
                     if (edge["left"], edge["right"]) == ("n00", "n01"))
    live = next(a for a in live_edge["answers"] if a["candidate_id"] == "c_cur_00")
    assert live["editable"] is True      # id AND pair exist in the live queue
    assert live["source"] == ""          # candidate_source.csv missing: degrade


def test_weakest_edge_needs_one_shared_metric_and_model(chain_dataset):
    root, current, _ = chain_dataset
    store = make_store(chain_dataset)
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    assert detail["metric_status"] == "sortable"
    # the two rounds recorded different paths with identical bytes: the hash
    # key must unify them, and the h_neg "different" answer (cosine 0.01) must
    # not steal the weakest badge from the true weakest same-edge
    assert detail["metric_key"]["name"] == "cosine"
    assert detail["metric_key"]["model"].startswith("sha256:")
    assert detail["metric_key"]["model_path"]
    assert detail["weakest_index"] == 11          # cosine 0.55 on edge n11-n12
    # a different model in one round breaks comparability; the mtime snapshot
    # must pick the rewritten report up without a restart
    report_file = root / "review" / "hist" / "mine.report.json"
    report_file.write_text(json.dumps({"model": str(root / "models" / "other.onnx")}))
    # force a distinct mtime: the rewrite can land in the same timestamp tick
    os.utime(report_file, ns=(report_file.stat().st_atime_ns,
                              report_file.stat().st_mtime_ns + 1_000_000))
    detail = transitive_conflict(store.conflict_report(refresh=True))["conflict_detail"]
    assert detail["metric_status"] == "mixed_metrics"
    assert detail["weakest_index"] is None


def test_metric_directions():
    def edge(name, value, direction, model="m", label="same"):
        return {"answers": [{"metric_name": name, "metric_value": value,
                             "metric_direction": direction, "model_or_source": model,
                             "label": label}]}
    # cosine: smaller = weaker
    index, status, key = weakest_edge_index([
        edge("cosine", 0.9, "lower_is_weaker"),
        edge("cosine", 0.4, "lower_is_weaker"),
        edge("cosine", 0.7, "lower_is_weaker")])
    assert (index, status) == (1, "sortable") and key["name"] == "cosine"
    # distance: larger = weaker
    index, status, _ = weakest_edge_index([
        edge("distance", 0.9, "higher_is_weaker"),
        edge("distance", 1.4, "higher_is_weaker"),
        edge("distance", 0.7, "higher_is_weaker")])
    assert (index, status) == (1, "sortable")
    # mixed models refuse to rank
    index, status, _ = weakest_edge_index([
        edge("cosine", 0.9, "lower_is_weaker", model="m1"),
        edge("cosine", 0.4, "lower_is_weaker", model="m2")])
    assert (index, status) == (None, "mixed_metrics")
    # an edge without any measurement refuses to rank
    index, status, _ = weakest_edge_index([
        edge("cosine", 0.9, "lower_is_weaker"), {"answers": []}])
    assert (index, status) == (None, "missing_metrics")
    # a measurement on a non-supporting answer neither ranks nor counts as a
    # measurement: different/unclear verdicts are not witnesses of a same-edge
    index, status, _ = weakest_edge_index([
        edge("cosine", 0.9, "lower_is_weaker"),
        edge("cosine", 0.01, "lower_is_weaker", label="different")])
    assert (index, status) == (None, "missing_metrics")


def test_candidate_metric_column_mapping():
    assert candidate_metric({"cosine": "0.7"})["direction"] == "lower_is_weaker"
    assert candidate_metric({"cosine_similarity": "0.7"})["direction"] == "lower_is_weaker"
    assert candidate_metric({"distance": "1.2"})["direction"] == "higher_is_weaker"
    assert candidate_metric({"centroid_distance": "1.2"})["direction"] == "higher_is_weaker"
    assert candidate_metric({"cosine": "", "rank_score": "0.5"}) is None


def test_background_recompute_keeps_detail(chain_dataset):
    store = make_store(chain_dataset)
    store.conflict_report()
    store._recompute_conflicts()          # what the invalidation thread runs
    for conflict in store._conflicts["conflicts"]:
        assert "conflict_detail" in conflict and "chain" in conflict


def test_contradiction_merges_all_physical_evidence(chain_dataset):
    store = make_store(chain_dataset)
    report = store.conflict_report()
    # every chain-bearing conflict carries the SAME merged endpoint evidence,
    # regardless of which check produced it
    for kind in ("transitive_negative", "temporal_overlap", "covisible_merge"):
        found = [item for item in report["conflicts"] if item["kind"] == kind]
        assert found, f"expected a {kind} conflict"
        contradiction = found[0]["conflict_detail"]["contradiction"]
        assert contradiction["negative_row_count"] == 2
        assert {row["evidence"] for row in contradiction["negative_rows"]} \
            == {"covisible_tracks_cross_crops", "covisible_proven_different"}
        assert contradiction["temporal"]["video"] == "v_overlap.mp4"
        assert contradiction["temporal"]["overlap_sec"] == pytest.approx(3.0)
        assert contradiction["covisible"]["frames"] == 7


def test_reviews_outside_review_dir_join_provenance(chain_dataset):
    root, current, _ = chain_dataset
    extra = root / "extra" / "answers.csv"
    store = Store(root, current, root / "pairs.csv", [current, extra])
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    edge = next(e for e in detail["edges"] if (e["left"], e["right"]) == ("n05", "n06"))
    answer = next(a for a in edge["answers"] if a["candidate_id"] == "x_extra_05")
    assert answer["file"] == "extra/answers.csv"
    assert answer["editable"] is False
    # the extra round has no recorded model, so its metric lives under its own
    # key and must not break the frozen-model chain ranking
    assert detail["metric_status"] == "sortable"
    assert detail["weakest_index"] == 11


# ---------------------------------------------------------------- decisions

def test_live_answer_decides_the_edge_and_needs_no_archive(chain_dataset):
    """The three buttons on an edge card must show what is already on record."""
    store = make_store(chain_dataset)
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    edge = next(e for e in detail["edges"] if (e["left"], e["right"]) == ("n00", "n01"))
    decision = edge["decision"]
    assert decision["verdict"] == "same"            # the button the page fills in
    assert decision["candidate_id"] == "c_cur_00"   # the row a click rewrites
    assert decision["action"] == "label"            # nothing baked stands in the way
    assert decision["archives"] == [] and decision["physical"] == []
    assert decision["conflicting"] is False


def test_baked_edge_names_the_rows_a_verdict_would_archive(chain_dataset):
    store = make_store(chain_dataset)
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    edge = next(e for e in detail["edges"] if (e["left"], e["right"]) == ("n02", "n03"))
    decision = edge["decision"]
    assert decision["verdict"] == "same"            # highlighted from pairs.csv
    assert decision["candidate_id"] == ""           # no live row to relabel
    assert decision["action"] == "revise"
    assert [row["line"] for row in decision["archives"]] == [3]
    assert decision["physical"] == []
    # the historical "different" answer on this pair exists but does not win:
    # what pairs.csv holds is what the dataset is actually using
    assert any(answer["label"] == "different" for answer in edge["answers"])


def test_endpoint_decision_surfaces_evidence_the_web_cannot_overrule(chain_dataset):
    store = make_store(chain_dataset)
    detail = transitive_conflict(store.conflict_report())["conflict_detail"]
    decision = detail["contradiction"]["decision"]
    assert decision["verdict"] == "different" and decision["action"] == "label"
    assert decision["archives"] == []
    assert {row["evidence"] for row in decision["physical"]} \
        == {"covisible_tracks_cross_crops", "covisible_proven_different"}


def answer(label, editable=False, candidate_id="c", round_name="r1"):
    return {"label": label, "editable": editable, "candidate_id": candidate_id,
            "round": round_name}


def base_row(label, evidence, line=2):
    return {"line": line, "label": label, "evidence": evidence, "batch": "", "split": "train"}


def test_edge_decision_precedence():
    # a live answer outranks both the manifest and every earlier round
    decision = edge_decision([answer("different", editable=True, candidate_id="live"),
                              answer("same")],
                             [base_row(1, "reviewed_model_mined_same")])
    assert (decision["verdict"], decision["candidate_id"]) == ("different", "live")
    assert decision["action"] == "revise"      # the baked row still has to fall
    # without a live answer the manifest decides, not the historical round
    assert edge_decision([answer("different")],
                         [base_row(1, "reviewed_x")])["verdict"] == "same"
    # history alone decides only while it agrees with itself
    lone = edge_decision([answer("unclear"), answer("unclear", round_name="r2")], [])
    assert (lone["verdict"], lone["action"]) == ("unclear", "label")
    assert "r1" in lone["origin"] and "r2" in lone["origin"]


def test_edge_decision_highlights_nothing_when_sources_disagree():
    for decision in (edge_decision([answer("same"), answer("different")], []),
                     edge_decision([], [base_row(1, "reviewed_x"),
                                        base_row(0, "reviewed_y", 3)])):
        assert decision["verdict"] == "" and decision["conflicting"] is True
    # two live rows on one pair disagreeing: pick neither, name both
    two_live = edge_decision([answer("same", editable=True, candidate_id="c1"),
                              answer("different", editable=True, candidate_id="c2")], [])
    assert two_live["verdict"] == "" and two_live["conflicting"] is True
    assert "c1" in two_live["origin"] and "c2" in two_live["origin"]
    blank = edge_decision([], [])
    assert (blank["verdict"], blank["conflicting"], blank["action"]) == ("", False, "label")
