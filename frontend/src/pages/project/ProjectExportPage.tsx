import { useEffect, useState } from "react";
import { useParams } from "wouter";

import { ApiError, exportUrl, listTaskTypes, type TaskTypeInfo } from "../../api/client";
import { summaryProgress } from "../../project-meta";
import { ProjectHeader, ProjectStateGate } from "./ProjectHeader";
import { useProject } from "./useProject";

function filenameFrom(response: Response, fallback: string): string {
  const header = response.headers.get("Content-Disposition") ?? "";
  return /filename="?([^";]+)"?/.exec(header)?.[1] ?? fallback;
}

export function ProjectExportPage() {
  const { projectId = "" } = useParams();
  const { state, reload } = useProject(projectId);
  const [typeInfo, setTypeInfo] = useState<TaskTypeInfo | null>(null);
  const [format, setFormat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const taskType = state.kind === "ready" ? state.project.task_type : "";
  useEffect(() => {
    if (!taskType) return;
    let active = true;
    listTaskTypes().then(
      (types) => {
        if (!active) return;
        const info = types.find((type) => type.type === taskType) ?? null;
        setTypeInfo(info);
        setFormat((current) => current || info?.export_formats[0]?.format || "");
      },
      (caught: unknown) => active && setError(caught instanceof Error ? caught.message : "未知错误"),
    );
    return () => {
      active = false;
    };
  }, [taskType]);

  if (state.kind !== "ready") {
    return <ProjectStateGate onRetry={() => void reload()} projectId={projectId} state={state} />;
  }
  const { project } = state;
  const progress = summaryProgress(project.summary);

  // Fetched rather than a plain link so a failed export shows its reason here
  // instead of downloading the error JSON as if it were the result.
  async function download() {
    setBusy(true);
    setError("");
    try {
      const response = await globalThis.fetch(exportUrl(project.id, format));
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as { detail?: { message?: string } } | null;
        throw new ApiError(response.status, payload?.detail?.message ?? "导出失败");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filenameFrom(response, `${project.id}-${format}`);
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "未知错误");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page page-narrow">
      <ProjectHeader project={project} section="导出" />
      {progress && progress.done === 0 && <p className="submit-hint">还没有标注结果，导出的文件将不含任何标注。</p>}
      {progress && progress.done > 0 && progress.done < progress.total && (
        <p className="muted">
          已完成 {progress.done} / {progress.total}，导出只包含已标注的部分。
        </p>
      )}
      <section className="card">
        <h2>导出格式</h2>
        {!typeInfo && !error && <p className="muted">正在读取…</p>}
        {typeInfo && (
          <div aria-label="导出格式" className="type-menu" role="radiogroup">
            {typeInfo.export_formats.map((option) => (
              <label className={option.format === format ? "type-option selected" : "type-option"} key={option.format}>
                <input
                  checked={option.format === format}
                  name="export-format"
                  onChange={() => setFormat(option.format)}
                  type="radio"
                />
                <span className="type-option-text">
                  <strong>{option.label}</strong>
                  <span>
                    <code>{option.format}</code> · 契约 <code title="导出文件已按此版本化契约校验">{option.contract}</code>
                  </span>
                </span>
              </label>
            ))}
          </div>
        )}
        {error && (
          <p className="inline-error" role="alert">
            {error}
          </p>
        )}
        <footer className="form-actions">
          <button className="button-primary" disabled={!format || busy} onClick={() => void download()} type="button">
            {busy ? "正在生成…" : "下载"}
          </button>
        </footer>
      </section>
    </div>
  );
}
