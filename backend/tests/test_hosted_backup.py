import json

import pytest
from annotation_platform.hosted_backup import backup, restore, verify


def test_full_data_restore_and_no_overwrite(tmp_path):
    root = tmp_path / "owned"
    (root / "users" / "alice" / "projects" / "p" / "data").mkdir(parents=True)
    image = root / "users" / "alice" / "projects" / "p" / "data" / "image.png"
    image.write_bytes(b"asset")
    (root / "legacy-import.json").write_text('{"sourceDigest":"old"}')
    saved, recovered = tmp_path / "backup", tmp_path / "restored"
    with pytest.raises(ValueError): backup(root, saved)
    report = backup(root, saved, quiesced=True)
    assert len(report["files"]) == 2
    assert verify(saved) == report
    restore(saved, recovered)
    assert (recovered / image.relative_to(root)).read_bytes() == b"asset"
    assert (recovered / "legacy-import.json").read_bytes() == (root / "legacy-import.json").read_bytes()
    with pytest.raises(ValueError): restore(saved, recovered)
    image.write_bytes(b"new")
    assert (recovered / image.relative_to(root)).read_bytes() == b"asset"


def test_corruption_links_unlisted_files_and_paths_are_rejected(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    (root / "projects.yaml").write_text("projects: []")
    saved = tmp_path / "backup"
    backup(root, saved, quiesced=True)
    (saved / "extra").write_text("unknown")
    with pytest.raises(ValueError): verify(saved)
    (saved / "extra").unlink()
    (saved / "projects.yaml").write_text("changed")
    with pytest.raises(ValueError): restore(saved, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    (saved / "projects.yaml").unlink()
    (saved / "projects.yaml").symlink_to(root / "projects.yaml")
    with pytest.raises(ValueError): verify(saved)
    manifest = json.loads((saved / "annotation-backup.json").read_text())
    manifest["files"][0]["path"] = "../escape"
    (saved / "annotation-backup.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError): verify(saved)


def test_ai_database_integrity_counts_and_restore(tmp_path):
    from annotation_platform.hosted_ai import job_database
    from annotation_platform.hosted import user_workspace
    root = tmp_path / "owned"
    workspace = user_workspace(root, "alice")
    with job_database(workspace) as connection:
        connection.execute("INSERT INTO jobs (id,project_id,request_json,source_sha256,source_revision,image_size_json,source_polygons_json,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                           ("job", "project", "{}", "a" * 64, 0, "{}", "[]", "running", "time"))
    saved, recovered = tmp_path / "backup", tmp_path / "restored"
    report = backup(root, saved, quiesced=True)
    assert list(report["databases"].values()) == [1]
    restore(saved, recovered)
    with job_database(user_workspace(recovered, "alice")) as connection:
        assert connection.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0] == "running"
    report["databases"][next(iter(report["databases"]))] = 2
    (saved / "annotation-backup.json").write_text(json.dumps(report))
    with pytest.raises(ValueError, match="database counts"): verify(saved)
