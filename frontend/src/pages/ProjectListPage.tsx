import { useEffect, useState } from "react";
import { Link } from "wouter";

import { listProjects, type ProjectListItem } from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; projects: ProjectListItem[] }
  | { kind: "error"; message: string };

const statusLabels: Record<string, string> = {
  missing: "数据缺失",
  empty: "等待数据",
  needs_mining: "等待候选挖掘",
  reviewing: "标注中",
  reviewed: "已完成",
};

export function ProjectListPage() {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let active = true;
    listProjects().then(
      (projects) => active && setState({ kind: "ready", projects }),
      (error: unknown) =>
        active &&
        setState({
          kind: "error",
          message: error instanceof Error ? error.message : "未知错误",
        }),
    );
    return () => {
      active = false;
    };
  }, []);

  return (
    <>
      <section className="hero">
        <p className="eyebrow">Projects</p>
        <h1>选择一个标注项目</h1>
        <p>项目、任务配置和标注结果都保留在你的本地文件系统中。</p>
      </section>

      {state.kind === "loading" && <LoadingState />}
      {state.kind === "error" && <ErrorState message={state.message} />}
      {state.kind === "ready" && state.projects.length === 0 && (
        <section className="state-panel">
          <p className="eyebrow">暂无项目</p>
          <h2>在 projects.yaml 中注册第一个项目</h2>
          <p>保存配置后刷新页面即可读取，无需创建用户或连接云存储。</p>
        </section>
      )}
      {state.kind === "ready" && state.projects.length > 0 && (
        <section className="project-grid" aria-label="项目列表">
          {state.projects.map((project) => (
            <Link className="project-card" to={`/projects/${project.id}`} key={project.id}>
              <div className="card-topline">
                <span className="task-chip">{project.task_type}</span>
                <span className={`status status-${project.status}`}>
                  {statusLabels[project.status] ?? project.status}
                </span>
              </div>
              <h2>{project.name}</h2>
              <p className="project-id">{project.id}</p>
              <p className="path" title={project.root}>
                {project.root}
              </p>
              <span className="open-link">打开项目 →</span>
            </Link>
          ))}
        </section>
      )}
    </>
  );
}
