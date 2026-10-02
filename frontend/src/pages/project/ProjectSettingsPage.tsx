import { useCallback, useEffect, useState } from "react";
import { useLocation, useParams } from "wouter";

import {
  deleteProject,
  getSettings,
  updateConfigText,
  updateSettings,
  type ProjectSettings,
  type SettingsValues,
} from "../../api/client";
import { missingRequired, SettingsForm } from "../../components/settings/SettingsForm";
import { useProjects } from "../../components/shell/ProjectsContext";
import { ProjectHeader, ProjectStateGate } from "./ProjectHeader";
import { useProject } from "./useProject";

function message(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}

export function ProjectSettingsPage() {
  const { projectId = "" } = useParams();
  const [, navigate] = useLocation();
  const { reload: reloadProjects } = useProjects();
  const { state, reload } = useProject(projectId);
  const [settings, setSettings] = useState<ProjectSettings | null>(null);
  const [loadError, setLoadError] = useState("");
  const [name, setName] = useState("");
  const [values, setValues] = useState<SettingsValues>({});
  const [configText, setConfigText] = useState("");
  const [busy, setBusy] = useState<"" | "form" | "config" | "delete">("");
  const [notice, setNotice] = useState<{ kind: "ok" | "error"; text: string; scope: string } | null>(null);
  const [confirmName, setConfirmName] = useState("");

  const apply = useCallback((loaded: ProjectSettings) => {
    setSettings(loaded);
    setName(loaded.name);
    setValues(loaded.values as SettingsValues);
    setConfigText(loaded.config_text);
  }, []);

  useEffect(() => {
    let active = true;
    getSettings(projectId).then(
      (loaded) => active && apply(loaded),
      (error: unknown) => active && setLoadError(message(error)),
    );
    return () => {
      active = false;
    };
  }, [apply, projectId]);

  if (state.kind !== "ready") {
    return <ProjectStateGate onRetry={() => void reload()} projectId={projectId} state={state} />;
  }
  const { project } = state;

  if (!settings) {
    return (
      <div className="page page-narrow">
        <ProjectHeader project={project} section="属性" />
        {loadError ? <p className="inline-error" role="alert">{loadError}</p> : <p className="muted">正在读取属性…</p>}
      </div>
    );
  }

  const initial = settings.values as SettingsValues;
  const missing = [...(name.trim() ? [] : ["项目名称"]), ...missingRequired(settings.fields, values)];
  const dirty = name.trim() !== settings.name || JSON.stringify(values) !== JSON.stringify(initial);

  async function saveForm() {
    if (!settings || missing.length || !dirty) return;
    setBusy("form");
    setNotice(null);
    try {
      // Only send what changed: unchanged server-only values would be rejected
      // as an attempt to edit them anyway.
      const changed: SettingsValues = {};
      for (const [key, value] of Object.entries(values)) {
        if (JSON.stringify(value) !== JSON.stringify(initial[key])) changed[key] = value;
      }
      apply(await updateSettings(projectId, { name: name.trim(), settings: changed }));
      setNotice({ kind: "ok", text: "已保存。", scope: "form" });
      await Promise.all([reload(), reloadProjects()]);
    } catch (error) {
      setNotice({ kind: "error", text: message(error), scope: "form" });
    } finally {
      setBusy("");
    }
  }

  async function saveConfig() {
    setBusy("config");
    setNotice(null);
    try {
      apply(await updateConfigText(projectId, configText));
      setNotice({ kind: "ok", text: "配置文件已保存。", scope: "config" });
      await reload();
    } catch (error) {
      setNotice({ kind: "error", text: message(error), scope: "config" });
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    setBusy("delete");
    setNotice(null);
    try {
      await deleteProject(projectId);
      await reloadProjects();
      navigate("/");
    } catch (error) {
      setNotice({ kind: "error", text: message(error), scope: "delete" });
      setBusy("");
    }
  }

  const noticeFor = (scope: string) =>
    notice?.scope === scope ? (
      <p className={notice.kind === "ok" ? "inline-ok" : "inline-error"} role={notice.kind === "error" ? "alert" : "status"}>
        {notice.text}
      </p>
    ) : null;

  return (
    <div className="page page-narrow">
      <ProjectHeader project={project} section="属性" />

      {settings.annotated && (
        <p className="submit-hint">项目已有标注结果：类别/标签只能在末尾追加，部分属性不能再修改。</p>
      )}

      <section className="card">
        <div className="settings-form">
          <div className="form-group">
            <div className="form-field">
              <label htmlFor="project-name">项目名称</label>
              <input id="project-name" maxLength={120} onChange={(event) => setName(event.target.value)} value={name} />
            </div>
            <div className="form-field">
              <span className="form-label">项目 ID</span>
              <code className="readonly-value">{settings.id}</code>
            </div>
          </div>
        </div>
        <SettingsForm
          annotated={settings.annotated}
          fields={settings.fields}
          initial={initial}
          onChange={(key, value) => setValues((current) => ({ ...current, [key]: value }))}
          values={values}
        />
        {noticeFor("form")}
        <footer className="form-actions">
          {missing.length > 0 && <span className="form-actions-hint">还需填写：{missing.join("、")}</span>}
          <button className="button-secondary" disabled={!dirty || busy !== ""} onClick={() => apply(settings)} type="button">
            还原
          </button>
          <button
            className="button-primary"
            disabled={!dirty || missing.length > 0 || busy !== ""}
            onClick={() => void saveForm()}
            type="button"
          >
            {busy === "form" ? "正在保存…" : "保存属性"}
          </button>
        </footer>
      </section>

      <details className="card card-collapsible">
        <summary>高级：直接编辑配置文件</summary>
        <p className="field-help">
          <code>{settings.config_path}</code>。数据目录、标注结果位置及会执行外部程序的路径不能在这里修改。
        </p>
        <textarea
          aria-label="配置文件内容"
          className="code-editor"
          onChange={(event) => setConfigText(event.target.value)}
          rows={Math.min(30, Math.max(8, configText.split("\n").length + 1))}
          spellCheck={false}
          value={configText}
        />
        {noticeFor("config")}
        <footer className="form-actions">
          <button
            className="button-primary"
            disabled={configText === settings.config_text || busy !== ""}
            onClick={() => void saveConfig()}
            type="button"
          >
            {busy === "config" ? "正在保存…" : "保存配置文件"}
          </button>
        </footer>
      </details>

      <section className="card danger-zone">
        <h2>删除项目</h2>
        <p>
          {settings.data_source.mode === "managed"
            ? "将删除项目配置、上传的数据和全部标注结果，无法恢复。"
            : `将删除项目配置。关联的目录 ${settings.data_source.path} 不会被删除，其中的标注结果文件也会保留。`}
        </p>
        <label className="form-label" htmlFor="confirm-delete">
          输入项目名称“{settings.name}”以确认
        </label>
        <div className="danger-row">
          <input id="confirm-delete" onChange={(event) => setConfirmName(event.target.value)} value={confirmName} />
          <button
            className="button-danger"
            disabled={confirmName !== settings.name || busy !== ""}
            onClick={() => void remove()}
            type="button"
          >
            {busy === "delete" ? "正在删除…" : "删除项目"}
          </button>
        </div>
        {noticeFor("delete")}
      </section>
    </div>
  );
}
