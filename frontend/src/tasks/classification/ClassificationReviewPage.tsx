import { useMemo, useState } from "react";
import { useParams } from "wouter";

import {
  OptionList,
  PanelSection,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { DIGIT_KEYS, MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import { ItemStage, isTextItem } from "../ItemStage";
import { QueueFallback, itemText, summaryStrings, useTaskQueue } from "../useTaskQueue";

export function ClassificationReviewPage() {
  const { projectId = "" } = useParams();
  const [selected, setSelected] = useState<string[]>([]);
  const queue = useTaskQueue(projectId, {
    taskType: "classification",
    wrongType: "该项目不是分类任务。",
    onLoad: () => setSelected([]),
  });
  const { ready, item, submitting, submitError } = queue;
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
    if (await queue.submit(itemText(item, "item_id"), { labels: selected })) setSelected([]);
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
      remaining={ready.queue.total}
      stage={<ItemStage item={item} projectId={projectId} />}
      title={isTextItem(item) ? "文本分类" : "图像分类"}
      unit={isTextItem(item) ? "篇" : "张"}
    />
  );
}
