import { useEffect, useState } from "react";
import { Link, useParams } from "wouter";

import { ApiError, getProject, type ProjectDetail } from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { statusLabels, summaryProgress, taskEntries, taskLabel } from "../project-meta";
import { ReIDActions } from "../tasks/reid/ReIDActions";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; project: ProjectDetail }
  | { kind: "missing" }
  | { kind: "error"; message: string };

const summaryLabels: Record<string, string> = {
  config: "配置文件",
  dataset: "数据目录",
  annotations: "标注结果",
  depth_maps: "基线深度图",
  categories: "类别",
  labels: "标签",
  mode: "模式",
  total: "图像总数",
  annotated: "已标注",
  labelled: "已标注",
  captioned: "已描述",
  segmented: "已分割",
  edited: "已编辑",
  pending: "待处理",
  exists: "数据存在",
  identities: "身份数",
  tracks: "轨迹数",
  pairs: "候选对",
  rounds: "审核轮次",
  live_round: "当前轮次",
};

function SummaryValue({ value }: { value: unknown }) {
  if (Array.isArray(value) && value.every((entry) => typeof entry === "string")) {
    if (value.length === 0) return <span className="project-id">—</span>;
    return (
      <span className="tag-list">
        {value.map((entry) => (
          <span className="tag" key={entry}>{entry}</span>
        ))}
      </span>
    );
  }
  if (typeof value === "boolean") return <>{value ? "是" : "否"}</>;
  if (typeof value === "string" && value.startsWith("/")) return <span className="path">{value}</span>;
  if (value === "" || value == null) return <span className="project-id">—</span>;
  return <>{typeof value === "object" ? JSON.stringify(value) : String(value)}</>;
}

export function ProjectDetailPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    getProject(projectId).then(
      (project) => active && setState({ kind: "ready", project }),
      (error: unknown) => {
        if (!active) return;
        setState(
          error instanceof ApiError && error.status === 404
            ? { kind: "missing" }
            : {
                kind: "error",
                message: error instanceof Error ? error.message : "未知错误",
              },
        );
      },
    );
    return () => {
      active = false;
    };
  }, [projectId]);

  if (state.kind === "loading") return <LoadingState>正在读取项目详情…</LoadingState>;
  if (state.kind === "error") return <ErrorState message={state.message} />;
  if (state.kind === "missing") {
    return (
      <section className="state-panel">
        <p className="eyebrow">404</p>
        <h1>项目不存在</h1>
        <p>注册表中没有 ID 为“{projectId}”的项目。</p>
        <Link className="text-link" to="/">
          返回项目列表
        </Link>
      </section>
    );
  }

  const { project } = state;
  const entry = taskEntries[project.task_type];
  const progress = summaryProgress(project.summary);
  const percent = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  return (
    <>
      <Link className="back-link" to="/">
        ← 所有项目
      </Link>
      <section className="detail-heading">
        <div>
          <p className="eyebrow">{taskLabel(project.task_type)}</p>
          <div className="detail-title-row">
            <h1>{project.name}</h1>
            <span className={`status status-${project.status}`} title={project.status}>
              {statusLabels[project.status] ?? project.status}
            </span>
          </div>
          <p className="project-id">{project.id}</p>
          <p className="path">{project.root}</p>
        </div>
        {entry && project.status === "reviewing" && (
          <Link className="primary-link" to={`/projects/${project.id}/${entry.path}`}>
            {entry.action} →
          </Link>
        )}
      </section>
      {progress && (
        <section className="detail-card detail-progress" aria-label="标注进度">
          <div className="detail-progress-numbers">
            <span>
              已完成 <strong>{progress.done}</strong> / {progress.total}
            </span>
            <span>{percent}%</span>
          </div>
          <div
            aria-valuemax={progress.total}
            aria-valuemin={0}
            aria-valuenow={progress.done}
            className="progress-track"
            role="progressbar"
          >
            <span style={{ width: `${percent}%` }} />
          </div>
        </section>
      )}
      <article className="detail-card">
        <h2>任务摘要</h2>
        <dl>
          {Object.entries(project.summary).map(([key, value]) => (
            <div className="summary-row" key={key}>
              <dt>
                {summaryLabels[key] ?? null}
                <code>{key}</code>
              </dt>
              <dd>
                <SummaryValue value={value} />
              </dd>
            </div>
          ))}
        </dl>
      </article>
      {project.task_type === "reid" && <ReIDActions projectId={project.id} />}
    </>
  );
}
