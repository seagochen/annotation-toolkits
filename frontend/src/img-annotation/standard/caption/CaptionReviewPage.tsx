import { useState } from "react";
import { useParams } from "wouter";

import { PanelSection, SubmitBar, TaskWorkspace } from "../../common/workspace/TaskWorkspace";
import { MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../common/workspace/useHotkeys";
import { summaryProgress } from "../../../project-meta";
import { ImageStrip } from "../../common/ImageStrip";
import { ItemStage, isTextItem } from "../../common/ItemStage";
import { QueueFallback, itemText, useResetOnItem, useTaskQueue } from "../../common/useTaskQueue";

// Same limits as caption_task.py: captions vs. text generated for a document.
const MAX_CAPTION_LENGTH = 2000;
const MAX_GENERATED_TEXT_LENGTH = 20000;

export function CaptionReviewPage() {
  const { projectId = "" } = useParams();
  const [caption, setCaption] = useState("");
  const queue = useTaskQueue(projectId, {
    taskType: "captioning",
    wrongType: "该项目不是描述 / 文本生成任务。",
  });
  const { ready, item, submitting, submitError } = queue;
  useResetOnItem(item ? itemText(item, "item_id") : "", () => setCaption(""));
  const trimmed = caption.trim();

  async function submit() {
    if (!item || !trimmed) return;
    await queue.submit(itemText(item, "item_id"), { caption: trimmed });
  }

  const hotkeys: Hotkey[] = [
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续（输入时也可用）",
      allowInText: true,
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (!ready || !item) {
    return (
      <QueueFallback
        doneDescription="当前没有待处理条目，所有结果均已原子写入本地 JSON。"
        doneTitle="描述 / 文本生成已完成"
        loading="正在读取描述队列…"
        projectId={projectId}
        queue={queue}
      />
    );
  }

  const imagePath = itemText(item, "image_path");
  const isText = isTextItem(item);
  const limit = isText ? MAX_GENERATED_TEXT_LENGTH : MAX_CAPTION_LENGTH;
  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!trimmed}
          error={submitError}
          hint={isText ? "先输入文本" : "先输入描述文本"}
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hotkeys={hotkeys}
      panel={
        <PanelSection title={isText ? "生成文本（翻译、摘要等）" : "描述文本"}>
          <textarea
            aria-label={isText ? "生成文本" : "图像描述"}
            autoFocus
            className="panel-textarea"
            disabled={submitting}
            key={itemText(item, "item_id")}
            maxLength={limit}
            onChange={(event) => setCaption(event.target.value)}
            placeholder={isText ? "根据左侧文本写出译文、摘要等…" : "描述这张图像的内容…"}
            rows={isText ? 16 : 8}
            value={caption}
          />
          <p className="char-count">{trimmed.length} / {limit}</p>
        </PanelSection>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      rawData={{ caption: trimmed }}
      remaining={ready.queue.total}
      stage={<ItemStage item={item} projectId={projectId} />}
      strip={<ImageStrip dirty={trimmed !== ""} projectId={projectId} queue={queue} title={isText ? "文档" : "图像"} />}
      title={isText ? "文本生成" : "图像描述"}
      unit={isText ? "篇" : "张"}
    />
  );
}
