import { useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../api/client";
import { PanelSection, TaskWorkspace } from "../../components/workspace/TaskWorkspace";
import { useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import { QueueFallback, itemText as text, useTaskQueue, type QueueItem } from "../useTaskQueue";

type Verdict = "same" | "different" | "unclear";

const verdicts: { value: Verdict; key: string; label: string; hint: string }[] = [
  { value: "same", key: "1", label: "同一人", hint: "Same" },
  { value: "different", key: "2", label: "不同人", hint: "Different" },
  { value: "unclear", key: "3", label: "不确定", hint: "Unclear" },
];

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
  const [notes, setNotes] = useState("");
  // Which verdict is being saved, and which one failed (for "retry").
  const [saving, setSaving] = useState<Verdict | null>(null);
  const [failed, setFailed] = useState<Verdict | null>(null);
  const queue = useTaskQueue(projectId, {
    taskType: "reid",
    wrongType: "该项目不是 ReID 审核任务。",
    onLoad: () => setFailed(null),
  });
  const { ready, item, submitError } = queue;

  async function submit(verdict: Verdict) {
    if (!item || saving) return;
    setSaving(verdict);
    setFailed(null);
    const saved = await queue.submit(text(item, "candidate_id"), { label: verdict, notes });
    setSaving(null);
    if (saved) setNotes("");
    else setFailed(verdict);
  }

  const hotkeys: Hotkey[] = verdicts.map((choice) => ({
    keys: [choice.key],
    display: choice.key,
    description: choice.label,
    run: () => {
      if (!saving) void submit(choice.value);
    },
  }));
  useHotkeys(hotkeys, Boolean(item));

  if (!ready || !item) {
    return (
      <QueueFallback
        doneDescription="当前没有待审核的候选。所有判定均已写入本地 CSV。"
        doneTitle="候选队列已完成"
        loading="正在读取审核队列…"
        projectId={projectId}
        queue={queue}
      />
    );
  }

  return (
    <TaskWorkspace
      footer={
        <>
          <div className="verdict-grid">
            {verdicts.map((choice) => (
              <button
                className={`verdict verdict-${choice.value}`}
                disabled={saving !== null}
                key={choice.value}
                onClick={() => void submit(choice.value)}
                type="button"
              >
                <kbd>{choice.key}</kbd>
                <span>{saving === choice.value ? "正在保存…" : choice.label}</span>
                <small>{choice.hint}</small>
              </button>
            ))}
          </div>
          {failed && submitError && (
            <div className="submit-error" role="alert">
              <span>保存失败：{submitError}</span>
              <div>
                <button
                  disabled={saving !== null}
                  onClick={() => void submit(failed)}
                  type="button"
                >
                  重试保存
                </button>
                <button onClick={() => void queue.reload()} type="button">刷新队列</button>
              </div>
            </div>
          )}
        </>
      }
      hotkeys={hotkeys}
      panel={
        <PanelSection title="判定">
          <p className="panel-note">两段轨迹是否属于同一人？对比服装、体型与时序后作答。</p>
          <div className="review-controls">
            <label htmlFor="review-notes">备注（可选）</label>
            <textarea
              disabled={saving !== null}
              id="review-notes"
              onChange={(event) => setNotes(event.target.value)}
              placeholder="记录遮挡、服装或时序等判断依据"
              rows={4}
              value={notes}
            />
          </div>
        </PanelSection>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      remaining={ready.queue.total}
      stage={
        <div className="comparison-grid">
          <IdentityGallery item={item} projectId={projectId} side={1} />
          <IdentityGallery item={item} projectId={projectId} side={2} />
        </div>
      }
      title="ReID 成对审核"
      unit="条"
    />
  );
}
