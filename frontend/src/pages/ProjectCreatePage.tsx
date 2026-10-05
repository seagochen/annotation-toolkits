import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useParams } from "wouter";

import {
  createProject,
  listTaskTypes,
  type SettingsValues,
  type TaskTypeInfo,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { defaultValues, missingRequired, SettingsForm } from "../components/settings/SettingsForm";
import { useProjects } from "../components/shell/ProjectsContext";

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; type: TaskTypeInfo }
  | { kind: "unknown" }
  | { kind: "error"; message: string };

export function ProjectCreatePage() {
  const { taskType = "" } = useParams();
  const [, navigate] = useLocation();
  const { reload, startNewProject } = useProjects();
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [name, setName] = useState("");
  const [values, setValues] = useState<SettingsValues>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    setState({ kind: "loading" });
    listTaskTypes().then(
      (types) => {
        if (!active) return;
        const type = types.find((candidate) => candidate.type === taskType);
        if (!type) return setState({ kind: "unknown" });
        setValues(defaultValues(type.fields));
        setState({ kind: "ready", type });
      },
      (caught: unknown) =>
        active && setState({ kind: "error", message: caught instanceof Error ? caught.message : "未知错误" }),
    );
    return () => {
      active = false;
    };
  }, [taskType]);

  if (state.kind === "loading") return <LoadingState>正在读取任务类型…</LoadingState>;
  if (state.kind === "error") return <ErrorState message={state.message} />;
  if (state.kind === "unknown") {
    return (
      <section className="state-panel">
        <h1>不支持的任务类型</h1>
        <p>“{taskType}”不是可用的标注任务。</p>
        <button onClick={startNewProject} type="button">重新选择</button>
      </section>
    );
  }

  const { type } = state;
  const missing = [...(name.trim() ? [] : ["项目名称"]), ...missingRequired(type.fields, values)];

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (missing.length || saving) return;
    setSaving(true);
    setError("");
    try {
      const project = await createProject(name.trim(), type.type, values);
      await reload();
      navigate(`/projects/${project.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "未知错误");
      setSaving(false);
    }
  }

  return (
    <form className="page page-narrow" onSubmit={(event) => void submit(event)}>
      <header className="page-header">
        <div>
          <p className="page-crumb">
            <span>新建项目</span>
            <span aria-hidden="true">/</span>
            <span className="task-chip">{type.label}</span>
          </p>
          <h1>项目属性</h1>
          <p className="page-lead">{type.description}</p>
        </div>
      </header>

      <section className="card">
        <div className="settings-form">
          <div className="form-group">
            <div className="form-field">
              <label htmlFor="project-name">
                项目名称<span aria-hidden="true" className="required"> *</span>
              </label>
              <input
                autoFocus
                id="project-name"
                maxLength={120}
                onChange={(event) => setName(event.target.value)}
                placeholder="例如：路口行人检测 2026-10"
                value={name}
              />
            </div>
          </div>
        </div>
        <SettingsForm
          fields={type.fields}
          onChange={(key, value) => setValues((current) => ({ ...current, [key]: value }))}
          values={values}
        />
      </section>

      {error && (
        <p className="inline-error" role="alert">
          创建失败：{error}
        </p>
      )}
      <footer className="form-actions">
        {missing.length > 0 && <span className="form-actions-hint">还需填写：{missing.join("、")}</span>}
        <Link className="button-secondary" to="/">
          取消
        </Link>
        <button className="button-primary" disabled={missing.length > 0 || saving} type="submit">
          {saving ? "正在创建…" : "创建项目"}
        </button>
      </footer>
    </form>
  );
}
