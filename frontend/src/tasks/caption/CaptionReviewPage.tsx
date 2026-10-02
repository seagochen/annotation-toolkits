import { useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../api/client";
import { PanelSection, SubmitBar, TaskWorkspace } from "../../components/workspace/TaskWorkspace";
import { MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import { QueueFallback, itemText, useTaskQueue } from "../useTaskQueue";

const MAX_CAPTION_LENGTH = 2000;

export function CaptionReviewPage() {
  const { projectId = "" } = useParams();
  const [caption, setCaption] = useState("");
  const queue = useTaskQueue(projectId, {
    taskType: "captioning",
    wrongType: "该项目不是图像描述任务。",
    onLoad: () => setCaption(""),
  });
  const { ready, item, submitting, submitError } = queue;
  const trimmed = caption.trim();

  async function submit() {
    if (!item || !trimmed) return;
    if (await queue.submit(itemText(item, "item_id"), { caption: trimmed })) setCaption("");
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
        doneDescription="当前没有待描述图像，所有结果均已原子写入本地 JSON。"
        doneTitle="图像描述已完成"
        loading="正在读取描述队列…"
        projectId={projectId}
        queue={queue}
      />
    );
  }

  const imagePath = itemText(item, "image_path");
  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!trimmed}
          error={submitError}
          hint="先输入描述文本"
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hotkeys={hotkeys}
      panel={
        <PanelSection title="描述文本">
          <textarea
            aria-label="图像描述"
            autoFocus
            className="panel-textarea"
            disabled={submitting}
            key={itemText(item, "item_id")}
            maxLength={MAX_CAPTION_LENGTH}
            onChange={(event) => setCaption(event.target.value)}
            placeholder="描述这张图像的内容…"
            rows={8}
            value={caption}
          />
          <p className="char-count">{trimmed.length} / {MAX_CAPTION_LENGTH}</p>
        </PanelSection>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      remaining={ready.queue.total}
      stage={
        <figure className="stage-image">
          <img alt={imagePath} src={projectFileUrl(projectId, imagePath)} />
        </figure>
      }
      title="图像描述"
    />
  );
}
