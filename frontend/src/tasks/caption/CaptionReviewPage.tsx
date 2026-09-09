import { useCallback, useEffect, useState } from "react";
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

  if (state.kind === "loading") return <LoadingState>正在读取描述队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <section className="state-panel review-complete">
        <p className="eyebrow">Queue complete</p>
        <h1>图像描述已完成</h1>
        <p>当前没有待描述图像，所有结果均已原子写入本地 JSON。</p>
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
          <p className="eyebrow">Image captioning</p>
          <h1>为这张图像撰写描述</h1>
        </div>
        <div className="queue-count">
          <strong>{state.queue.total}</strong>
          <span>张待描述</span>
        </div>
      </div>

      <div className="classification-grid">
        <figure className="classification-image">
          <img alt={imagePath} src={projectFileUrl(projectId, imagePath)} />
          <figcaption>{imagePath}</figcaption>
        </figure>
        <div className="label-selector">
          <h2>描述文本</h2>
          <textarea
            aria-label="图像描述"
            disabled={submitting}
            maxLength={MAX_CAPTION_LENGTH}
            onChange={(event) => setCaption(event.target.value)}
            placeholder="描述这张图像的内容…"
            rows={6}
            value={caption}
          />
          <p className="caption-length">{trimmed.length} / {MAX_CAPTION_LENGTH}</p>
          <button
            className="classification-submit"
            disabled={!trimmed || submitting}
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
