import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "wouter";

import {
  getProject,
  getQueue,
  projectFileUrl,
  submitAnnotation,
  type ProjectDetail,
  type QueueResponse,
} from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import {
  CompleteState,
  OptionList,
  PanelSection,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { DIGIT_KEYS, MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";

type QueueItem = QueueResponse["items"][number];
type ReadyState = { project: ProjectDetail; queue: QueueResponse };
type PageState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyState)
  | { kind: "error"; message: string };

function itemText(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

function configuredLabels(project: ProjectDetail): string[] {
  const value = project.summary.labels;
  return Array.isArray(value)
    ? value.filter((label): label is string => typeof label === "string")
    : [];
}

export function ClassificationReviewPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [selected, setSelected] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitError("");
    try {
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId),
      ]);
      if (project.task_type !== "classification") {
        setState({ kind: "error", message: "该项目不是图像分类任务。" });
        return;
      }
      setSelected([]);
      setState({ kind: "ready", project, queue });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "未知错误",
      });
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const item = state.kind === "ready" ? state.queue.items[0] : undefined;
  const labels = useMemo(
    () => (state.kind === "ready" ? configuredLabels(state.project) : []),
    [state],
  );
  const mode = state.kind === "ready" ? String(state.project.summary.mode) : "single";

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
    if (!item || !selected.length || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      await submitAnnotation(projectId, itemText(item, "item_id"), {
        labels: selected,
      });
      const queue = await getQueue(projectId);
      setState((current) =>
        current.kind === "ready" ? { ...current, queue } : current,
      );
      setSelected([]);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "未知错误");
    } finally {
      setSubmitting(false);
    }
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

  if (state.kind === "loading") return <LoadingState>正在读取分类队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <CompleteState
        description="当前没有待分类图像，所有结果均已原子写入本地 JSON。"
        projectId={projectId}
        title="图像分类已完成"
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
      progress={summaryProgress(state.project.summary, state.queue.total)}
      projectId={projectId}
      projectName={state.project.name}
      remaining={state.queue.total}
      stage={
        <figure className="stage-image">
          <img alt={imagePath} src={projectFileUrl(projectId, imagePath)} />
        </figure>
      }
      title="图像分类"
    />
  );
}
