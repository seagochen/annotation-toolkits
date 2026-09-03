"""Operating-domain guards: same-day, same-camera only.

Covers the id parser, the mining gate and the conflict check that together
keep cross-day / cross-camera relations out of the dataset (user decision of
2026-09-02: cross-day pairs are unusable in either direction, and production
keeps one gallery per camera).
"""

from reid_annotation_tool import conflicts as conflict_engine
from reid_annotation_tool.core import PAIR_FIELDS, domain_allows, identity_domain

from conftest import (CANDIDATE_FIELDS, IDENTITY_FIELDS, TRACK_FIELDS, write_csv)

PAIR_FIELDS_WITH_BATCH = PAIR_FIELDS + ("batch",)

# same camera/day, other day, other camera, second pair for the review cases
A = "cam16_d20260830_v001_t00001"
B = "cam16_d20260829_v001_t00002"   # same camera, previous day
C = "cam36_d20260830_v002_t00003"   # same day, other camera
D = "cam16_d20260830_v003_t00004"
E = "cam16_d20260829_v004_t00005"   # cross-day partner for D
LEGACY = "d20260823_v038_t00018"    # batch-01 style: day but no camera prefix


def test_identity_domain_parsing():
    assert identity_domain(A) == ("cam16", "20260830")
    assert identity_domain(LEGACY) == ("", "20260823")
    assert identity_domain("cam16-2026-08-30-210002_t00001") == ("cam16", "20260830")
    assert identity_domain("seg-20260823-225003_t00001") == ("", "20260823")
    assert identity_domain("a") is None            # foreign naming: ungated
    assert identity_domain("track_00042") is None


def test_domain_allows_gates_day_and_camera_separately():
    assert domain_allows(A, D, False, False) is True            # same day+camera
    assert domain_allows(A, B, False, False) is False           # cross day
    assert domain_allows(A, B, True, False) is True             # explicitly allowed
    assert domain_allows(A, C, False, False) is False           # cross camera
    assert domain_allows(A, C, False, True) is True             # explicitly allowed
    assert domain_allows("a", "b", False, False) is True        # unparseable: ungated
    assert domain_allows(LEGACY, "d20260823_v040_t00054", False, False) is True


def domain_dataset(tmp_path):
    """Minimal dataset whose only irregularities are cross-domain relations."""
    identities, tracks = [], []
    for index, name in enumerate([A, B, C, D, E, "z1", "z2"]):
        path = f"images/train/{name}/00.jpg"
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(b"jpeg")
        identities.append({"img_path": path, "person_id": name, "split": "train",
                           "video": f"v_{index}.mp4", "track_id": index, "class_id": 0,
                           "timestamp": f"{100 * index:.3f}"})
        tracks.append({"person_id": name, "split": "train", "video": f"v_{index}.mp4",
                       "track_id": index, "class_id": 0, "start": f"{100 * index:.3f}",
                       "end": f"{100 * index + 5:.3f}", "frames": 10, "crops": 1,
                       "recovered": 0, "status": "accepted", "reason": ""})
    write_csv(tmp_path / "identities.csv", IDENTITY_FIELDS, identities)
    write_csv(tmp_path / "tracks.csv", TRACK_FIELDS, tracks)

    def base(left, right, label):
        return {"img1": f"images/train/{left}/00.jpg", "img2": f"images/train/{right}/00.jpg",
                "label": label, "split": "train", "evidence": "test_evidence",
                "person_id1": left, "person_id2": right, "gap_sec": "1", "batch": "b"}

    # cross-day label=0 AND cross-camera label=1: both are errors — the label
    # does not matter, the relation itself is out of domain
    write_csv(tmp_path / "pairs.csv", PAIR_FIELDS_WITH_BATCH,
              [base(A, B, 0), base(A, C, 1)])

    def review(cid, left, right, label, kind="cross_track"):
        return {"candidate_id": cid, "kind": kind, "split": "train",
                "person_id1": left, "person_id2": right,
                "img1": f"images/train/{left}/00.jpg", "img2": f"images/train/{right}/00.jpg",
                "time_gap_sec": "10", "cosine": "0.9", "rank_score": "0.9",
                "review_label": label, "review_notes": ""}

    write_csv(tmp_path / "review.csv", CANDIDATE_FIELDS, [
        review("r_unclear", D, E, "unclear"),        # cross day, unclear: warning only
        review("r_foreign", "z1", "z2", "same"),     # unparseable ids: not judged
        review("r_purity", A, A, "different", kind="track_purity"),  # exempt
    ])
    return tmp_path


def test_cross_domain_relations_are_flagged(tmp_path):
    root = domain_dataset(tmp_path)
    report = conflict_engine.report(root, root / "pairs.csv", [root / "review.csv"])
    flagged = {tuple(item["identities"]): item for item in report["conflicts"]
               if item["kind"] in {"cross_day_pair", "cross_camera_pair"}}

    day = flagged[(B, A)]                       # relation() sorts the pair
    assert day["kind"] == "cross_day_pair" and day["severity"] == "error"
    assert day["witnesses"] == ["base:test_evidence"]
    assert day["detail"]["labels"] == ["pairs.csv label=0"]

    camera = flagged[(A, C)]
    assert camera["kind"] == "cross_camera_pair" and camera["severity"] == "error"
    assert camera["detail"]["labels"] == ["pairs.csv label=1"]

    unclear = flagged[(E, D)]
    assert unclear["severity"] == "warning"     # unclear never enters training
    assert unclear["witnesses"] == ["r_unclear"]

    # unparseable ids and track_purity rows are never judged
    assert ("z1", "z2") not in flagged
    assert all(A != left or A != right for left, right in flagged if left == right)
    assert len(flagged) == 3


def test_clean_same_domain_dataset_stays_silent(tmp_path):
    root = domain_dataset(tmp_path)
    # rewrite pairs with only in-domain relations: no domain conflicts remain
    write_csv(root / "pairs.csv", PAIR_FIELDS_WITH_BATCH,
              [{"img1": f"images/train/{A}/00.jpg", "img2": f"images/train/{D}/00.jpg",
                "label": 1, "split": "train", "evidence": "test_evidence",
                "person_id1": A, "person_id2": D, "gap_sec": "1", "batch": "b"}])
    write_csv(root / "review.csv", CANDIDATE_FIELDS, [])
    report = conflict_engine.report(root, root / "pairs.csv", [root / "review.csv"])
    assert not [item for item in report["conflicts"]
                if item["kind"] in {"cross_day_pair", "cross_camera_pair"}]
