import { useEffect, useState } from "react";
import { Link, useParams } from "wouter";

import { ApiError, getProject, type ProjectDetail } from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { ReIDActions } from "../tasks/reid/ReIDActions";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; project: ProjectDetail }
  | { kind: "missing" }
  | { kind: "error"; message: string };

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
  return (
    <>
      <Link className="back-link" to="/">
        ← 所有项目
      </Link>
      <section className="detail-heading">
        <div>
          <p className="eyebrow">{project.task_type}</p>
          <h1>{project.name}</h1>
          <p className="project-id">{project.id}</p>
        </div>
        <span className={`status status-${project.status}`}>{project.status}</span>
      </section>
      {project.task_type === "reid" && project.status === "reviewing" && (
        <Link className="primary-link" to={`/projects/${project.id}/review`}>
          开始审核候选 →
        </Link>
      )}
      {project.task_type === "classification" && project.status === "reviewing" && (
        <Link className="primary-link" to={`/projects/${project.id}/classify`}>
          开始图像分类 →
        </Link>
      )}
      {project.task_type === "caption" && project.status === "reviewing" && (
        <Link className="primary-link" to={`/projects/${project.id}/caption`}>
          开始图像描述 →
        </Link>
      )}
      <section className="detail-grid">
        <article className="detail-card">
          <h2>本地数据</h2>
          <dl>
            <dt>数据目录</dt>
            <dd className="path">{project.root}</dd>
            <dt>当前状态</dt>
            <dd>{project.status}</dd>
          </dl>
        </article>
        <article className="detail-card">
          <h2>任务摘要</h2>
          <dl>
            {Object.entries(project.summary).map(([key, value]) => (
              <div className="summary-row" key={key}>
                <dt>{key}</dt>
                <dd>{typeof value === "object" ? JSON.stringify(value) : String(value)}</dd>
              </div>
            ))}
          </dl>
        </article>
      </section>
      {project.task_type === "reid" && <ReIDActions projectId={project.id} />}
    </>
  );
}
