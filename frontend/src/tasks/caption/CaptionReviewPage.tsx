import { useCallback, useEffect, useState } from "react";
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
  PanelSection,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";

type QueueItem = QueueResponse["items"][number];
type ReadyState = { project: ProjectDetail; queue: QueueResponse };
type PageState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyState)
  | { kind: "error"; message: string };

const MAX_CAPTION_LENGTH = 2000;

function itemText(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

export function CaptionReviewPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [caption, setCaption] = useState("");
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
      if (project.task_type !== "captioning") {
        setState({ kind: "error", message: "该项目不是图像描述任务。" });
        return;
      }
      setCaption("");
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
  const trimmed = caption.trim();

  async function submit() {
    if (!item || !trimmed || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      await submitAnnotation(projectId, itemText(item, "item_id"), {
        caption: trimmed,
      });
      const queue = await getQueue(projectId);
      setState((current) =>
        current.kind === "ready" ? { ...current, queue } : current,
      );
      setCaption("");
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "未知错误");
    } finally {
      setSubmitting(false);
    }
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

  if (state.kind === "loading") return <LoadingState>正在读取描述队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <CompleteState
        description="当前没有待描述图像，所有结果均已原子写入本地 JSON。"
        projectId={projectId}
        title="图像描述已完成"
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
      progress={summaryProgress(state.project.summary, state.queue.total)}
      projectId={projectId}
      projectName={state.project.name}
      remaining={state.queue.total}
      stage={
        <figure className="stage-image">
          <img alt={imagePath} src={projectFileUrl(projectId, imagePath)} />
        </figure>
      }
      title="图像描述"
    />
  );
}
