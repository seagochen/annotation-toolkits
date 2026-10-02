import type { ReactNode } from "react";
import { Link, useParams } from "wouter";

import { AnnotateIcon, ExportIcon, ImportIcon, SettingsIcon } from "../../components/shell/icons";
import { summaryProgress, taskEntries } from "../../project-meta";
import { ReIDActions } from "../../tasks/reid/ReIDActions";
import { ProjectHeader, ProjectStateGate } from "./ProjectHeader";
import { useProject } from "./useProject";

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

function StepCard({
  to,
  icon,
  title,
  description,
  primary = false,
  disabledReason,
}: {
  to: string;
  icon: ReactNode;
  title: string;
  description: string;
  primary?: boolean;
  disabledReason?: string;
}) {
  const className = `step-card${primary ? " primary" : ""}${disabledReason ? " disabled" : ""}`;
  const body = (
    <>
      <span className="step-icon">{icon}</span>
      <span className="step-text">
        <strong>{title}</strong>
        <span>{disabledReason ?? description}</span>
      </span>
    </>
  );
  if (disabledReason) {
    return (
      <div aria-disabled="true" className={className}>
        {body}
      </div>
    );
  }
  return (
    <Link className={className} to={to}>
      {body}
    </Link>
  );
}

export function ProjectOverviewPage() {
  const { projectId = "" } = useParams();
  const { state, reload } = useProject(projectId);
  if (state.kind !== "ready") {
    return <ProjectStateGate onRetry={() => void reload()} projectId={projectId} state={state} />;
  }

  const { project } = state;
  const base = `/projects/${project.id}`;
  const entry = taskEntries[project.task_type];
  const progress = summaryProgress(project.summary);
  const percent = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  const isReid = project.task_type === "reid";
  const noData = !isReid && progress !== null && progress.total === 0;
  const annotateBlocked =
    project.status === "reviewing"
      ? undefined
      : project.status === "reviewed"
        ? "全部已完成，没有待处理的条目。"
        : isReid
          ? "需要先完成抽取与候选挖掘（见下方 ReID 流水线）。"
          : "还没有可标注的数据，请先导入。";

  return (
    <div className="page">
      <ProjectHeader project={project} />
      <p className="path page-path" title="数据目录">{project.root}</p>

      {progress && (
        <section aria-label="标注进度" className="card detail-progress">
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

      <section aria-label="项目操作" className="step-grid">
        <StepCard
          description={isReid ? "关联已有数据集目录，或从视频抽取。" : "上传图片或关联服务器上的目录。"}
          icon={<ImportIcon />}
          primary={noData}
          title="导入数据"
          to={`${base}/import`}
        />
        {entry && (
          <StepCard
            description={isReid ? "逐对判断两段轨迹是否为同一人。" : "逐张标注，结果实时写入本地。"}
            disabledReason={annotateBlocked}
            icon={<AnnotateIcon />}
            primary={!noData && !annotateBlocked}
            title={entry.action}
            to={`${base}/${entry.path}`}
          />
        )}
        <StepCard description="名称、类别等任务属性。" icon={<SettingsIcon />} title="编辑属性" to={`${base}/settings`} />
        <StepCard description="下载标注结果。" icon={<ExportIcon />} title="导出" to={`${base}/export`} />
      </section>

      {isReid && <ReIDActions projectId={project.id} />}

      <article className="card">
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
    </div>
  );
}
