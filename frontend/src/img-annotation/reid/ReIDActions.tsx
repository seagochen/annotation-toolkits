import { useCallback, useEffect, useState } from "react";

import {
  getAction,
  listActions,
  startAction,
  type ActionListResponse,
  type ActionResponse,
} from "../../api/client";

const labels: Record<string, string> = {
  extract: "抽取数据",
  mine: "挖掘候选",
  check: "检查冲突",
  finalize: "生成定版清单",
  "purge-domain": "清理跨域关系",
  train: "启动训练",
};

function isActive(job: ActionResponse): boolean {
  return job.state === "queued" || job.state === "running";
}

export function ReIDActions({ projectId }: { projectId: string }) {
  const [data, setData] = useState<ActionListResponse | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState("");
  const [applyPurge, setApplyPurge] = useState(false);
  const [runTraining, setRunTraining] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setData(await listActions(projectId));
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "未知错误");
    }
  }, [projectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const active = data?.jobs.find(isActive);
  useEffect(() => {
    if (!active) return;
    const timer = window.setTimeout(async () => {
      try {
        const updated = await getAction(projectId, active.id);
        setData((current) =>
          current
            ? {
                ...current,
                jobs: current.jobs.map((job) =>
                  job.id === updated.id ? updated : job,
                ),
              }
            : current,
        );
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "未知错误");
      }
    }, 500);
    return () => window.clearTimeout(timer);
  }, [active, projectId]);

  async function run(action: string) {
    setStarting(action);
    setError("");
    const options: Record<string, boolean> =
      action === "purge-domain"
        ? { apply: applyPurge }
        : action === "train"
          ? { dry_run: !runTraining }
          : {};
    try {
      const job = await startAction(projectId, action, options);
      setData((current) =>
        current ? { ...current, jobs: [...current.jobs, job] } : current,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "未知错误");
    } finally {
      setStarting("");
    }
  }

  const jobs = [...(data?.jobs ?? [])].reverse();
  return (
    <section className="actions-panel" aria-labelledby="actions-title">
      <div className="actions-heading">
        <div>
          <p className="eyebrow">Pipeline</p>
          <h2 id="actions-title">ReID 项目动作</h2>
        </div>
        <button className="secondary-button" onClick={() => void refresh()} type="button">
          刷新状态
        </button>
      </div>

      {error && <p className="inline-error" role="alert">{error}</p>}
      {!data && !error && <p role="status">正在读取可用动作…</p>}
      {data && (
        <>
          <div className="action-grid">
            {data.actions.map((action) => (
              <button
                disabled={Boolean(active) || Boolean(starting)}
                key={action}
                onClick={() => void run(action)}
                type="button"
              >
                <strong>{starting === action ? "正在启动…" : labels[action] ?? action}</strong>
                <small>{action}</small>
              </button>
            ))}
          </div>
          <div className="action-options">
            <label>
              <input
                checked={applyPurge}
                onChange={(event) => setApplyPurge(event.target.checked)}
                type="checkbox"
              />
              purge-domain 真正写入（默认仅预览）
            </label>
            <label>
              <input
                checked={runTraining}
                onChange={(event) => setRunTraining(event.target.checked)}
                type="checkbox"
              />
              train 实际执行（默认 dry-run）
            </label>
          </div>
          <div className="job-history" aria-label="动作历史">
            {jobs.length === 0 && <p>尚未执行项目动作。</p>}
            {jobs.map((job) => (
              <article className={`job-card job-${job.state}`} key={job.id}>
                <div>
                  <strong>{labels[job.name] ?? job.name}</strong>
                  <span>{job.state}</span>
                </div>
                {job.log.length > 0 && <pre>{job.log.join("\n")}</pre>}
                {job.result && <code>{JSON.stringify(job.result)}</code>}
                {job.error && <p className="inline-error">{job.error}</p>}
              </article>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
