import json
from pathlib import Path

import pytest

from annotation_platform.task_types import (
    ExportRequest,
    QueueRequest,
    Submission,
    TaskConflictError,
    TaskOperationError,
)
from annotation_platform.text_span_task import TextSpanTaskType

# Astral-plane characters (the emoji) are one code point but two UTF-16 units:
# offsets must count the former.
NEWS = "张三在北京见到了李四。\n\n🎉 李四来自上海。"


def project_config(tmp_path: Path) -> Path:
    dataset = tmp_path / "texts"
    dataset.mkdir()
    (dataset / "news.txt").write_text(NEWS, encoding="utf-8")
    (dataset / "crlf.txt").write_bytes("﻿A\r\nB".encode("utf-8"))
    (dataset / "notes.md").write_text("not matched by the default pattern", encoding="utf-8")
    config = tmp_path / "text_span.yaml"
    config.write_text("dataset: ./texts\nlabels: [PER, LOC, EVENT]\n", encoding="utf-8")
    return config


def items(module, project, **filters):
    page = module.queue(project, QueueRequest(filters=filters))
    return {item["image_path"]: item for item in page.items}


def span(start, end, label):
    return {"start": start, "end": end, "label": label}


def test_queue_submit_reload_and_export(tmp_path):
    module = TextSpanTaskType()
    project = module.load(project_config(tmp_path))
    queued = items(module, project)
    assert sorted(queued) == ["crlf.txt", "news.txt"]
    news = queued["news.txt"]
    assert news["text"] == NEWS and news["text_error"] is None and news["spans"] == []
    # The BOM is dropped and CRLF kept, so offsets index exactly this string.
    assert queued["crlf.txt"]["text"] == "A\r\nB"

    shanghai = NEWS.index("上海")
    assert NEWS[shanghai : shanghai + 2] == "上海"
    spans = [
        span(shanghai, shanghai + 2, "LOC"),
        span(0, 2, "PER"),
        # Overlapping, nested and cross-paragraph spans are all allowed.
        span(0, len(NEWS), "EVENT"),
        span(3, 5, "LOC"),
        span(0, 2, "LOC"),
        span(8, 15, "EVENT"),
    ]
    saved = module.submit(project, Submission(news["item_id"], {"spans": spans}))
    assert saved.item["spans"] == [
        span(0, 2, "PER"), span(0, 2, "LOC"), span(0, len(NEWS), "EVENT"),
        span(3, 5, "LOC"), span(8, 15, "EVENT"), span(shanghai, shanghai + 2, "LOC"),
    ]
    assert saved.item["length"] == len(NEWS)
    assert saved.status.details["annotated"] == 1
    assert saved.status.details["labels"] == ["PER", "LOC", "EVENT"]

    reopened = TextSpanTaskType()
    project = reopened.load(tmp_path / "text_span.yaml")
    done = items(reopened, project, status="annotated")
    assert list(done) == ["news.txt"]
    assert done["news.txt"]["spans"] == saved.item["spans"]

    exported = reopened.export(project, ExportRequest(format="native"))
    document = json.loads(exported.artifacts[0].read_text(encoding="utf-8"))
    assert document["schema"] == 1
    assert document["items"][news["item_id"]]["spans"] == saved.item["spans"]
    assert document["history"][0]["item_id"] == news["item_id"]
    with pytest.raises(TaskOperationError, match="does not support"):
        reopened.export(project, ExportRequest(format="conll"))


def test_empty_result_is_valid_and_resubmission_is_idempotent(tmp_path):
    module = TextSpanTaskType()
    project = module.load(project_config(tmp_path))
    item_id = items(module, project)["crlf.txt"]["item_id"]
    assert module.submit(project, Submission(item_id, {"spans": []})).item["spans"] == []
    assert module.submit(project, Submission(item_id, {"spans": []})).item["spans"] == []
    with pytest.raises(TaskConflictError, match="already annotated"):
        module.submit(project, Submission(item_id, {"spans": [span(0, 1, "PER")]}))
    history = json.loads(
        (tmp_path / "texts" / ".annotations" / "text_span.json").read_text(encoding="utf-8")
    )["history"]
    assert len(history) == 1


@pytest.mark.parametrize(
    ("spans", "message"),
    [
        (None, "requires a `spans` list"),
        ([{"start": 0, "end": 1}], "exactly start, end and label"),
        ([{"start": 0, "end": 1, "label": "PER", "text": "张"}], "exactly start, end and label"),
        ([span(-1, 1, "PER")], "0 <= start < end"),
        ([span(2, 2, "PER")], "0 <= start < end"),
        ([span(3, 2, "PER")], "0 <= start < end"),
        ([span(0, len(NEWS) + 1, "PER")], f"<= {len(NEWS)}"),
        # UTF-16 length would allow one more position than code points do.
        ([span(0, len(NEWS.encode("utf-16-le")) // 2, "PER")], "0 <= start < end"),
        ([span(0.0, 1, "PER")], "must be an integer"),
        ([span(True, 2, "PER")], "must be an integer"),
        ([span("0", 1, "PER")], "must be an integer"),
        ([span(0, 1, "ORG")], "unknown text_span label"),
        ([span(0, 1, "PER"), span(0, 1, "PER")], "listed twice"),
    ],
)
def test_invalid_spans_are_rejected_and_nothing_is_saved(tmp_path, spans, message):
    module = TextSpanTaskType()
    project = module.load(project_config(tmp_path))
    item_id = items(module, project)["news.txt"]["item_id"]
    result = {} if spans is None else {"spans": spans}
    with pytest.raises(TaskOperationError, match=message):
        module.submit(project, Submission(item_id, result))
    assert not (tmp_path / "texts" / ".annotations" / "text_span.json").exists()


def test_undecodable_document_is_shown_with_its_error_and_cannot_be_annotated(tmp_path):
    module = TextSpanTaskType()
    project = module.load(project_config(tmp_path))
    (tmp_path / "texts" / "gbk.txt").write_bytes("中文".encode("gbk"))
    item = items(module, project)["gbk.txt"]
    assert item["text"] is None
    assert "not valid UTF-8" in item["text_error"]
    with pytest.raises(TaskOperationError, match="gbk.txt.*not valid UTF-8"):
        module.submit(project, Submission(item["item_id"], {"spans": []}))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("dataset: ./texts\n", "`labels` must be a non-empty list"),
        ("dataset: ./texts\nlabels: [A, A]\n", "unique"),
        ("dataset: ./texts\nlabels: [A]\nextra: 1\n", "unknown text_span config keys"),
        ("dataset: ./texts\nlabels: [A]\nannotations: ../x.json\n", "stay inside dataset"),
    ],
)
def test_invalid_config_is_rejected(tmp_path, body, message):
    (tmp_path / "texts").mkdir()
    config = tmp_path / "text_span.yaml"
    config.write_text(body, encoding="utf-8")
    with pytest.raises(TaskOperationError, match=message):
        TextSpanTaskType().load(config)


def test_configured_patterns_replace_the_default(tmp_path):
    config = project_config(tmp_path)
    config.write_text(
        config.read_text(encoding="utf-8") + "patterns: ['**/*.md']\n", encoding="utf-8"
    )
    module = TextSpanTaskType()
    assert list(items(module, module.load(config))) == ["notes.md"]
