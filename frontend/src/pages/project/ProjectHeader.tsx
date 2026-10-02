import type { ReactNode } from "react";
import { Link } from "wouter";

import type { ProjectDetail } from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import { statusLabels, taskLabel } from "../../project-meta";
import type { ProjectState } from "./useProject";

export function ProjectHeader({
  project,
  section,
  actions,
}: {
  project: ProjectDetail;
  /** Sub-page title; omitted on the overview. */
  section?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <p className="page-crumb">
          <span className="task-chip" title={project.task_type}>{taskLabel(project.task_type)}</span>
          {section && <Link to={`/projects/${project.id}`}>{project.name}</Link>}
        </p>
        <div className="page-title-row">
          <h1>{section ?? project.name}</h1>
          {!section && (
            <span className={`status status-${project.status}`} title={project.status}>
              {statusLabels[project.status] ?? project.status}
            </span>
          )}
        </div>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

/** Loading / 404 / error states shared by every project sub-page. */
export function ProjectStateGate({
  state,
  projectId,
  onRetry,
}: {
  state: Exclude<ProjectState, { kind: "ready" }>;
  projectId: string;
  onRetry: () => void;
}) {
  if (state.kind === "loading") return <LoadingState>正在读取项目…</LoadingState>;
  if (state.kind === "error") return <ErrorState message={state.message} onRetry={onRetry} />;
  return (
    <section className="state-panel">
      <p className="eyebrow">404</p>
      <h1>项目不存在</h1>
      <p>没有 ID 为“{projectId}”的项目，它可能已被删除。</p>
      <Link className="text-link" to="/">
        返回首页
      </Link>
    </section>
  );
}
