import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "wouter";

import {
  getProject,
  getQueue,
  projectFileUrl,
  submitAnnotation,
  type ProjectDetail,
  type QueueResponse,
} from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";

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

  if (state.kind === "loading") return <LoadingState>正在读取分类队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <section className="state-panel review-complete">
        <p className="eyebrow">Queue complete</p>
        <h1>图像分类已完成</h1>
        <p>当前没有待分类图像，所有结果均已原子写入本地 JSON。</p>
        <Link className="text-link" to={`/projects/${projectId}`}>返回项目详情</Link>
      </section>
    );
  }

  const imagePath = itemText(item, "image_path");
  return (
    <section className="classification-workspace">
      <div className="review-topbar">
        <div>
          <Link className="back-link" to={`/projects/${projectId}`}>← {state.project.name}</Link>
          <p className="eyebrow">Image classification · {mode}</p>
          <h1>为这张图像选择标签</h1>
        </div>
        <div className="queue-count">
          <strong>{state.queue.total}</strong>
          <span>张待分类</span>
        </div>
      </div>

      <div className="classification-grid">
        <figure className="classification-image">
          <img alt={imagePath} src={projectFileUrl(projectId, imagePath)} />
          <figcaption>{imagePath}</figcaption>
        </figure>
        <div className="label-selector">
          <h2>{mode === "multi" ? "可选择多个标签" : "选择一个标签"}</h2>
          <div role="group" aria-label="分类标签">
            {labels.map((label) => (
              <label className={selected.includes(label) ? "selected" : ""} key={label}>
                <input
                  checked={selected.includes(label)}
                  disabled={submitting}
                  name={mode === "single" ? "classification-label" : undefined}
                  onChange={() => toggle(label)}
                  type={mode === "single" ? "radio" : "checkbox"}
                />
                <span>{label}</span>
              </label>
            ))}
          </div>
          <button
            className="classification-submit"
            disabled={!selected.length || submitting}
            onClick={() => void submit()}
            type="button"
          >
            {submitting ? "正在保存…" : "保存并继续"}
          </button>
          {submitError && (
            <div className="submit-error" role="alert">
              <span>保存失败：{submitError}</span>
              <button onClick={() => void submit()} type="button">重试保存</button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
