import { useMemo, useState } from "react";
import { useParams } from "wouter";

import {
  OptionList,
  PanelSection,
  SubmitBar,
  TaskWorkspace,
} from "../../common/workspace/TaskWorkspace";
import { DIGIT_KEYS, MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../common/workspace/useHotkeys";
import { summaryProgress } from "../../../project-meta";
import { ImageStrip } from "../../common/ImageStrip";
import { ItemStage, isTextItem } from "../../common/ItemStage";
import { QueueFallback, itemText, summaryStrings, useResetOnItem, useTaskQueue } from "../../common/useTaskQueue";

export function ClassificationReviewPage() {
  const { projectId = "" } = useParams();
  const [selected, setSelected] = useState<string[]>([]);
  const queue = useTaskQueue(projectId, {
    taskType: "classification",
    wrongType: "该项目不是分类任务。",
  });
  const { ready, item, submitting, submitError } = queue;
  useResetOnItem(item ? itemText(item, "item_id") : "", () => setSelected([]));
  const labels = useMemo(() => (ready ? summaryStrings(ready.project, "labels") : []), [ready]);
  const mode = ready ? String(ready.project.summary.mode) : "single";

  function toggle(label: string) {
    setSelected((current) =>
      mode === "single"
        ? [label]
        : current.includes(label)
          ? current.filter((value) => value !== label)
          : [...current, label],
    );
  }

  async function submit() {
    if (!item || !selected.length) return;
    await queue.submit(itemText(item, "item_id"), { labels: selected });
  }

  const hotkeys: Hotkey[] = [
    {
      keys: DIGIT_KEYS,
      display: "1–9",
      description: mode === "multi" ? "切换第 N 个标签" : "选择第 N 个标签",
      run: (key) => {
        const label = labels[Number(key) - 1];
        if (label && !submitting) toggle(label);
      },
    },
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (!ready || !item) {
    return (
      <QueueFallback
        doneDescription="当前没有待分类条目，所有结果均已原子写入本地 JSON。"
        doneTitle="分类已完成"
        loading="正在读取分类队列…"
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
          disabled={!selected.length}
          error={submitError}
          hint={mode === "multi" ? "至少选择一个标签" : "先选择一个标签"}
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hotkeys={hotkeys}
      panel={
        <PanelSection title={mode === "multi" ? "标签（可多选）" : "标签（单选）"}>
          <OptionList
            disabled={submitting}
            label="分类标签"
            multiple={mode === "multi"}
            name="classification-label"
            onToggle={toggle}
            options={labels}
            selected={selected}
          />
        </PanelSection>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      rawData={{ labels: selected }}
      remaining={ready.queue.total}
      stage={<ItemStage item={item} projectId={projectId} />}
      strip={<ImageStrip
          dirty={selected.length > 0}
          projectId={projectId}
          queue={queue}
          title={isTextItem(item) ? "文档" : "图像"}
        />}
      title={isTextItem(item) ? "文本分类" : "图像分类"}
      unit={isTextItem(item) ? "篇" : "张"}
    />
  );
}
