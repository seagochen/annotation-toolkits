import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useParams } from "wouter";

import {
  getSettings,
  linkDirectory,
  listTaskTypes,
  uploadArchive,
  uploadImage,
  type ProjectSettings,
  type TaskTypeInfo,
} from "../../api/client";
import { useProjects } from "../../components/shell/ProjectsContext";
import { summaryProgress } from "../../project-meta";
import { ProjectHeader, ProjectStateGate } from "./ProjectHeader";
import { useProject } from "./useProject";

const IMAGE = /\.(jpe?g|png|webp)$/i;
const ZIP = /\.zip$/i;
const CONCURRENCY = 4;

type Picked = { file: File; path: string };
type UploadState = {
  total: number;
  done: number;
  failed: { path: string; message: string }[];
  skipped: number;
  archives: number;
  running: boolean;
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}

/** Keeps folder structure; strips characters the server rejects in a segment. */
function relativePath(file: File, fallback?: string): string {
  const raw = fallback || (file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name;
  return raw
    .split("/")
    .filter((segment) => segment && !segment.startsWith("."))
    .join("/");
}

/** Walks dropped folders (drag & drop gives entries, not a flat file list). */
async function filesFromDrop(event: DragEvent): Promise<Picked[]> {
  const entries = [...event.dataTransfer.items]
    .map((item) => item.webkitGetAsEntry?.())
    .filter((entry): entry is FileSystemEntry => Boolean(entry));
  if (!entries.length) {
    return [...event.dataTransfer.files].map((file) => ({ file, path: relativePath(file) }));
  }
  const picked: Picked[] = [];
  async function walk(entry: FileSystemEntry, prefix: string): Promise<void> {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
      picked.push({ file, path: relativePath(file, `${prefix}${file.name}`) });
      return;
    }
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    // readEntries returns at most ~100 entries per call; keep reading until empty.
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
      if (!batch.length) break;
      for (const child of batch) await walk(child, `${prefix}${entry.name}/`);
    }
  }
  for (const entry of entries) await walk(entry, "");
  return picked;
}

export function ProjectImportPage() {
  const { projectId = "" } = useParams();
  const { reload: reloadProjects } = useProjects();
  const { state, reload } = useProject(projectId);
  const [settings, setSettings] = useState<ProjectSettings | null>(null);
  const [typeInfo, setTypeInfo] = useState<TaskTypeInfo | null>(null);
  const [loadError, setLoadError] = useState("");
  const [upload, setUpload] = useState<UploadState | null>(null);
  const [dragging, setDragging] = useState(false);
  const [directory, setDirectory] = useState("");
  const [linking, setLinking] = useState(false);
  const [linkNotice, setLinkNotice] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const zipInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    Promise.all([getSettings(projectId), listTaskTypes()]).then(
      ([loaded, types]) => {
        if (!active) return;
        setSettings(loaded);
        setTypeInfo(types.find((type) => type.type === loaded.task_type) ?? null);
        if (loaded.data_source.mode === "directory") setDirectory(loaded.data_source.path);
      },
      (error: unknown) => active && setLoadError(message(error)),
    );
    return () => {
      active = false;
    };
  }, [projectId]);

  // webkitdirectory is not in React's input attribute types.
  useEffect(() => {
    folderInput.current?.setAttribute("webkitdirectory", "");
  }, [settings]);

  if (state.kind !== "ready") {
    return <ProjectStateGate onRetry={() => void reload()} projectId={projectId} state={state} />;
  }
  const { project } = state;
  const progress = summaryProgress(project.summary);
  const isReid = project.task_type === "reid";
  const modes = typeInfo?.import_modes ?? [];
  const managed = settings?.data_source.mode === "managed";
  const canUpload = Boolean(settings) && managed && modes.includes("upload");
  const canLink = modes.includes("directory");

  async function refreshAfterImport() {
    await Promise.all([reload(), reloadProjects()]);
  }

  async function importPicked(picked: Picked[]) {
    const images = picked.filter((item) => IMAGE.test(item.file.name) && item.path);
    const archives = picked.filter((item) => ZIP.test(item.file.name));
    const skipped = picked.length - images.length - archives.length;
    const next: UploadState = {
      total: images.length + archives.length,
      done: 0,
      failed: [],
      skipped,
      archives: archives.length,
      running: true,
    };
    setUpload(next);
    if (!next.total) {
      setUpload({ ...next, running: false });
      return;
    }

    const queue = [...images];
    const worker = async () => {
      for (let item = queue.shift(); item; item = queue.shift()) {
        const current = item;
        try {
          await uploadImage(projectId, current.path, current.file);
          setUpload((state) => state && { ...state, done: state.done + 1 });
        } catch (error) {
          setUpload(
            (state) =>
              state && { ...state, done: state.done + 1, failed: [...state.failed, { path: current.path, message: message(error) }] },
          );
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, images.length) }, worker));
    for (const archive of archives) {
      try {
        const result = await uploadArchive(projectId, archive.file);
        setUpload((state) => state && { ...state, done: state.done + 1, skipped: state.skipped + result.skipped });
      } catch (error) {
        setUpload(
          (state) =>
            state && { ...state, done: state.done + 1, failed: [...state.failed, { path: archive.path, message: message(error) }] },
        );
      }
    }
    setUpload((state) => state && { ...state, running: false });
    await refreshAfterImport();
  }

  function pickFrom(input: HTMLInputElement | null) {
    if (!input?.files) return;
    const picked = [...input.files].map((file) => ({ file, path: relativePath(file) }));
    input.value = "";
    void importPicked(picked);
  }

  async function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (!canUpload || upload?.running) return;
    void importPicked(await filesFromDrop(event));
  }

  async function link() {
    if (!directory.trim()) return;
    setLinking(true);
    setLinkNotice(null);
    try {
      const updated = await linkDirectory(projectId, directory.trim());
      setSettings(updated);
      setLinkNotice({ kind: "ok", text: "已关联。" });
      await refreshAfterImport();
    } catch (error) {
      setLinkNotice({ kind: "error", text: message(error) });
    } finally {
      setLinking(false);
    }
  }

  const percent = upload && upload.total ? Math.round((upload.done / upload.total) * 100) : 0;

  return (
    <div className="page page-narrow">
      <ProjectHeader project={project} section="导入数据" />

      <section className="card">
        <h2>当前数据</h2>
        {!settings && !loadError && <p className="muted">正在读取…</p>}
        {loadError && <p className="inline-error" role="alert">{loadError}</p>}
        {settings && (
          <dl className="fact-list">
            <div>
              <dt>来源</dt>
              <dd>{managed ? "平台托管目录（上传的数据）" : "关联的服务器目录"}</dd>
            </div>
            <div>
              <dt>位置</dt>
              <dd className="path">{settings.data_source.path}</dd>
            </div>
            {progress && !isReid && (
              <div>
                <dt>图像数量</dt>
                <dd>{progress.total}</dd>
              </div>
            )}
          </dl>
        )}
      </section>

      {canUpload && (
        <section className="card">
          <h2>上传</h2>
          <div
            className={dragging ? "dropzone dragging" : "dropzone"}
            onDragLeave={() => setDragging(false)}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDrop={(event) => void onDrop(event)}
          >
            <p className="dropzone-title">把图片、文件夹或 ZIP 压缩包拖到这里</p>
            <p className="muted">支持 JPG / PNG / WebP；同名文件会被覆盖，文件夹结构会保留。</p>
            <div className="dropzone-buttons">
              <button className="button-secondary" disabled={upload?.running} onClick={() => fileInput.current?.click()} type="button">
                选择图片
              </button>
              <button className="button-secondary" disabled={upload?.running} onClick={() => folderInput.current?.click()} type="button">
                选择文件夹
              </button>
              <button className="button-secondary" disabled={upload?.running} onClick={() => zipInput.current?.click()} type="button">
                上传 ZIP
              </button>
            </div>
            <input accept="image/jpeg,image/png,image/webp" hidden multiple onChange={(event) => pickFrom(event.currentTarget)} ref={fileInput} type="file" />
            <input hidden multiple onChange={(event) => pickFrom(event.currentTarget)} ref={folderInput} type="file" />
            <input accept=".zip,application/zip" hidden onChange={(event) => pickFrom(event.currentTarget)} ref={zipInput} type="file" />
          </div>
          {upload && (
            <div className="upload-status" role="status">
              <div className="detail-progress-numbers">
                <span>
                  {upload.running ? "正在上传" : "上传完成"} {upload.done} / {upload.total}
                  {upload.archives > 0 && `（含 ${upload.archives} 个压缩包）`}
                </span>
                <span>{percent}%</span>
              </div>
              <div className="progress-track">
                <span style={{ width: `${percent}%` }} />
              </div>
              {upload.skipped > 0 && <p className="muted">已跳过 {upload.skipped} 个非图片文件。</p>}
              {upload.failed.length > 0 && (
                <details className="upload-failures">
                  <summary>{upload.failed.length} 个文件失败</summary>
                  <ul>
                    {upload.failed.map((failure) => (
                      <li key={failure.path}>
                        <code>{failure.path}</code>：{failure.message}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {!upload.running && upload.done > upload.failed.length && (
                <Link className="text-link" to={`/projects/${project.id}`}>
                  返回概览开始标注 →
                </Link>
              )}
            </div>
          )}
        </section>
      )}

      {canLink && settings && (
        <section className="card">
          <h2>{isReid ? "关联数据集目录" : "关联服务器目录"}</h2>
          <p className="muted">
            {isReid
              ? "指向一个已有的 ReID 数据集目录（含 identities.csv 等）。不关联时，可在概览页用“抽取数据”从属性中设置的视频目录生成。"
              : "直接读取服务器上的图片目录，不复制文件；标注结果写在该目录的 .annotations 下。"}
            {managed && canUpload && " 只有在还没有上传任何数据时才能改为关联目录。"}
          </p>
          <div className="inline-form">
            <input
              aria-label="服务器目录路径"
              className="mono"
              onChange={(event) => setDirectory(event.target.value)}
              placeholder="/data/datasets/scene-a"
              spellCheck={false}
              value={directory}
            />
            <button className="button-primary" disabled={!directory.trim() || linking} onClick={() => void link()} type="button">
              {linking ? "正在关联…" : "关联"}
            </button>
          </div>
          {settings.import_roots.length > 0 && (
            <p className="field-help">
              允许的位置：{settings.import_roots.map((root) => <code key={root}>{root}</code>)}
            </p>
          )}
          {linkNotice && (
            <p className={linkNotice.kind === "ok" ? "inline-ok" : "inline-error"} role={linkNotice.kind === "error" ? "alert" : "status"}>
              {linkNotice.text}
            </p>
          )}
        </section>
      )}
    </div>
  );
}
