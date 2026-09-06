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

type Verdict = "same" | "different" | "unclear";
type QueueItem = QueueResponse["items"][number];
type ReadyState = { project: ProjectDetail; queue: QueueResponse };
type PageState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyState)
  | { kind: "error"; message: string };

const verdicts: { value: Verdict; key: string; label: string; hint: string }[] = [
  { value: "same", key: "1", label: "同一人", hint: "Same" },
  { value: "different", key: "2", label: "不同人", hint: "Different" },
  { value: "unclear", key: "3", label: "不确定", hint: "Unclear" },
];

function text(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

function gallery(item: QueueItem, side: 1 | 2): string[] {
  const value = item[`gallery${side}`];
  if (Array.isArray(value)) return value.filter((path): path is string => typeof path === "string");
  const fallback = text(item, `img${side}`);
  return fallback ? [fallback] : [];
}

function IdentityGallery({
  item,
  projectId,
  side,
}: {
  item: QueueItem;
  projectId: string;
  side: 1 | 2;
}) {
  const identity = text(item, `person_id${side}`);
  const images = gallery(item, side);
  return (
    <article className="identity-panel">
      <div className="identity-heading">
        <span>轨迹 {side === 1 ? "A" : "B"}</span>
        <strong>{identity || "未知轨迹"}</strong>
      </div>
      <div className="identity-gallery">
        {images.length ? (
          images.map((path) => (
            <img
              alt={`${identity} 的证据帧`}
              key={path}
              src={projectFileUrl(projectId, path)}
            />
          ))
        ) : (
          <div className="missing-image">无证据图像</div>
        )}
      </div>
    </article>
  );
}

export function ReIDReviewPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState<Verdict | null>(null);
  const [submitFailure, setSubmitFailure] = useState<{
    message: string;
    verdict: Verdict;
  } | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitFailure(null);
    try {
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId),
      ]);
      if (project.task_type !== "reid") {
        setState({ kind: "error", message: "该项目不是 ReID 审核任务。" });
        return;
      }
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
  const submit = useCallback(
    async (verdict: Verdict) => {
      if (!item || submitting) return;
      setSubmitting(verdict);
      setSubmitFailure(null);
      try {
        await submitAnnotation(projectId, text(item, "candidate_id"), verdict, notes);
        const queue = await getQueue(projectId);
        setState((current) =>
          current.kind === "ready" ? { ...current, queue } : current,
        );
        setNotes("");
      } catch (error) {
        setSubmitFailure({
          message: error instanceof Error ? error.message : "未知错误",
          verdict,
        });
      } finally {
        setSubmitting(null);
      }
    },
    [item, notes, projectId, submitting],
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches("input, textarea, select") || submitting) return;
      const verdict = verdicts.find((choice) => choice.key === event.key)?.value;
      if (verdict) void submit(verdict);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [submit, submitting]);

  if (state.kind === "loading") return <LoadingState>正在读取审核队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <section className="state-panel review-complete">
        <p className="eyebrow">Queue complete</p>
        <h1>候选队列已完成</h1>
        <p>当前没有待审核的候选。所有判定均已写入本地 CSV。</p>
        <Link className="text-link" to={`/projects/${projectId}`}>
          返回项目详情
        </Link>
      </section>
    );
  }

  return (
    <section className="review-workspace">
      <div className="review-topbar">
        <div>
          <Link className="back-link" to={`/projects/${projectId}`}>
            ← {state.project.name}
          </Link>
          <p className="eyebrow">ReID pair review</p>
          <h1>这两段轨迹属于同一人吗？</h1>
        </div>
        <div className="queue-count">
          <strong>{state.queue.total}</strong>
          <span>条待审核</span>
        </div>
      </div>

      <div className="comparison-grid">
        <IdentityGallery item={item} projectId={projectId} side={1} />
        <IdentityGallery item={item} projectId={projectId} side={2} />
      </div>

      <div className="review-controls">
        <label htmlFor="review-notes">备注（可选）</label>
        <textarea
          disabled={submitting !== null}
          id="review-notes"
          onChange={(event) => setNotes(event.target.value)}
          placeholder="记录遮挡、服装或时序等判断依据"
          rows={2}
          value={notes}
        />
        <div className="verdict-grid">
          {verdicts.map((choice) => (
            <button
              className={`verdict verdict-${choice.value}`}
              disabled={submitting !== null}
              key={choice.value}
              onClick={() => void submit(choice.value)}
              type="button"
            >
              <kbd>{choice.key}</kbd>
              <span>{submitting === choice.value ? "正在保存…" : choice.label}</span>
              <small>{choice.hint}</small>
            </button>
          ))}
        </div>
        {submitFailure && (
          <div className="submit-error" role="alert">
            <span>保存失败：{submitFailure.message}</span>
            <div>
              <button
                disabled={submitting !== null}
                onClick={() => void submit(submitFailure.verdict)}
                type="button"
              >
                重试保存
              </button>
              <button onClick={() => void load()} type="button">刷新队列</button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
