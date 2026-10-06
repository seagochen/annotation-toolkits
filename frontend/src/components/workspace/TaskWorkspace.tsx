import { useState, type ReactNode } from "react";
import { Link } from "wouter";

import type { Progress } from "../../project-meta";
import { categoryColor } from "./palette";
import { BackIcon, CodeIcon, KeyboardIcon, LabelsIcon, RedoIcon, UndoIcon } from "./tool-icons";
import { MOD_LABEL, type Hotkey } from "./useHotkeys";
import "./workspace.css";

export type ShortcutHint = Readonly<{ display: string; description: string }>;

/** ImageCanvas's built-in navigation, listed on every canvas page. */
export const CANVAS_HINTS: readonly ShortcutHint[] = [
  { display: "滚轮", description: "缩放" },
  { display: "空格 + 拖动", description: "平移画布" },
  { display: "0", description: "适应窗口（画布聚焦时）" },
];

/** One button of the canvas tool rail. */
export type WorkspaceTool = Readonly<{
  id: string;
  label: string;
  icon: ReactNode;
  /** Shown in the tooltip, e.g. "B". */
  shortcut?: string;
  active: boolean;
  onSelect: () => void;
}>;

export type HistoryControls = Readonly<{
  canUndo: boolean;
  canRedo: boolean;
  undo: () => void;
  redo: () => void;
}>;

type PanelTab = "annotate" | "shortcuts" | "raw";

/**
 * The full-viewport frame every annotation page shares, laid out like
 * Roboflow's annotator: on the left an icon rail switching the side panel
 * (annotation panel, shortcuts, raw result) above a fixed save footer; the
 * canvas in the middle with a floating vertical tool rail; and, when the page
 * passes one, the image list on the right.
 */
export function TaskWorkspace({
  projectId,
  projectName,
  title,
  fileName,
  progress,
  remaining,
  unit = "张",
  stage,
  panel,
  footer,
  hotkeys = [],
  hints = [],
  tools,
  history,
  toolOptions,
  strip,
  rawData,
}: {
  projectId: string;
  projectName: string;
  title: string;
  fileName?: string;
  progress: Progress | null;
  remaining: number;
  unit?: string;
  stage: ReactNode;
  panel: ReactNode;
  footer: ReactNode;
  hotkeys?: readonly Hotkey[];
  hints?: readonly ShortcutHint[];
  /** Canvas tools, in groups separated by a divider. */
  tools?: readonly (readonly WorkspaceTool[])[];
  history?: HistoryControls;
  /** Settings of the active tool (brush size…), floated beside the tool rail. */
  toolOptions?: ReactNode;
  /** The image list column (see tasks/ImageStrip). */
  strip?: ReactNode;
  /** The result as it would be saved, shown in the "原始数据" tab. */
  rawData?: unknown;
}) {
  const [tab, setTab] = useState<PanelTab>("annotate");
  const percent = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  const shortcuts = [...hotkeys, ...hints];
  const tabs: { id: PanelTab; label: string; icon: ReactNode }[] = [
    { id: "annotate", label: "标注", icon: <LabelsIcon /> },
    ...(shortcuts.length ? [{ id: "shortcuts" as const, label: "快捷键", icon: <KeyboardIcon /> }] : []),
    ...(rawData !== undefined ? [{ id: "raw" as const, label: "原始数据", icon: <CodeIcon /> }] : []),
  ];
  const activeTab = tabs.some((candidate) => candidate.id === tab) ? tab : "annotate";
  const hasRail = Boolean(tools?.length || history);

  return (
    <section className={strip ? "workspace has-strip" : "workspace"}>
      <aside aria-label="标注面板" className="workspace-side">
        <header className="workspace-head">
          <Link aria-label={`返回 ${projectName}`} className="workspace-back" to={`/projects/${projectId}`}>
            <BackIcon />
          </Link>
          <div className="workspace-where">
            <div className="workspace-crumb">
              <span className="workspace-project">{projectName}</span>
              <span aria-hidden="true">·</span>
              <h1>{title}</h1>
            </div>
            {fileName && (
              <span className="workspace-file" title={fileName}>
                {fileName}
              </span>
            )}
          </div>
        </header>
        <div className="workspace-side-body">
          <nav aria-label="面板" className="workspace-tabs">
            {tabs.map((candidate) => (
              <button
                aria-pressed={candidate.id === activeTab}
                className="workspace-tab"
                key={candidate.id}
                onClick={() => setTab(candidate.id)}
                type="button"
              >
                {candidate.icon}
                <span>{candidate.label}</span>
              </button>
            ))}
          </nav>
          <div className="workspace-panel">
            <div className="workspace-panel-content">
              {activeTab === "annotate" && panel}
              {activeTab === "shortcuts" && <ShortcutList shortcuts={shortcuts} />}
              {activeTab === "raw" && (
                <PanelSection title="原始数据">
                  <p className="panel-note">保存时提交的结果（未保存的修改也已反映在内）。</p>
                  <pre className="raw-data">{JSON.stringify(rawData, null, 2)}</pre>
                </PanelSection>
              )}
            </div>
            <div className="workspace-panel-footer">
              <div className="workspace-progress">
                {progress && (
                  <div
                    aria-label="标注进度"
                    aria-valuemax={progress.total}
                    aria-valuemin={0}
                    aria-valuenow={progress.done}
                    className="progress-track"
                    role="progressbar"
                  >
                    <span style={{ width: `${percent}%` }} />
                  </div>
                )}
                <div className="progress-text">
                  {progress && (
                    <span className="progress-label">
                      已完成 {progress.done} / {progress.total}
                    </span>
                  )}
                  <div className="queue-count">
                    <strong>{remaining}</strong>
                    <span>{unit}待处理</span>
                  </div>
                </div>
              </div>
              {footer}
            </div>
          </div>
        </div>
      </aside>
      <div className="workspace-stage">
        {stage}
        {hasRail && (
          <div aria-label="标注工具" aria-orientation="vertical" className="tool-rail" role="toolbar">
            {tools?.map((group, index) => (
              <div className="tool-group" key={index}>
                {group.map((tool) => (
                  <button
                    aria-label={tool.label}
                    aria-pressed={tool.active}
                    className="tool-button"
                    key={tool.id}
                    onClick={tool.onSelect}
                    title={tool.shortcut ? `${tool.label}（${tool.shortcut}）` : tool.label}
                    type="button"
                  >
                    {tool.icon}
                  </button>
                ))}
              </div>
            ))}
            {history && (
              <div className="tool-group">
                <button
                  aria-label="撤销"
                  className="tool-button"
                  disabled={!history.canUndo}
                  onClick={history.undo}
                  title={`撤销（${MOD_LABEL} + Z）`}
                  type="button"
                >
                  <UndoIcon />
                </button>
                <button
                  aria-label="重做"
                  className="tool-button"
                  disabled={!history.canRedo}
                  onClick={history.redo}
                  title={`重做（${MOD_LABEL} + Shift + Z）`}
                  type="button"
                >
                  <RedoIcon />
                </button>
              </div>
            )}
          </div>
        )}
        {toolOptions && (
          <div aria-label="工具选项" className={hasRail ? "tool-options" : "tool-options no-rail"} role="group">
            {toolOptions}
          </div>
        )}
      </div>
      {strip}
    </section>
  );
}

function ShortcutList({ shortcuts }: { shortcuts: readonly ShortcutHint[] }) {
  return (
    <PanelSection title="快捷键">
      <dl className="shortcut-list">
        {shortcuts.map((shortcut) => (
          <div key={`${shortcut.display}-${shortcut.description}`}>
            <dt>
              <kbd>{shortcut.display}</kbd>
            </dt>
            <dd>{shortcut.description}</dd>
          </div>
        ))}
      </dl>
    </PanelSection>
  );
}

/**
 * Roboflow's "Classes / Layers" switch: the panel's category legend and the
 * list of this item's shapes as two tabs under one "标注 N" heading.
 */
export function AnnotationTabs({
  count,
  classes,
  layers,
}: {
  count: number;
  classes: ReactNode;
  layers: ReactNode;
}) {
  const [tab, setTab] = useState<"classes" | "layers">("classes");
  return (
    <section className="panel-section">
      <div className="panel-section-heading annotation-heading">
        <h2>标注</h2>
        <span className="count-badge">{count}</span>
      </div>
      <div aria-label="标注视图" className="annotation-tabs" role="group">
        <button aria-pressed={tab === "classes"} onClick={() => setTab("classes")} type="button">
          类别
        </button>
        <button aria-pressed={tab === "layers"} onClick={() => setTab("layers")} type="button">
          图层
        </button>
      </div>
      {tab === "classes" ? classes : layers}
    </section>
  );
}

export function PanelSection({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="panel-section">
      <div className="panel-section-heading">
        <h2>{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/**
 * Category/label choices backed by real radio or checkbox inputs (so they
 * stay keyboard- and screen-reader-operable), with the colour swatch and the
 * digit hotkey shown alongside. Both decorations are aria-hidden so each
 * input's accessible name is exactly the option text.
 */
export function OptionList({
  label,
  name,
  options,
  selected,
  multiple = false,
  swatches = false,
  disabled = false,
  counts,
  onToggle,
}: {
  label: string;
  name: string;
  options: readonly string[];
  selected: readonly string[];
  multiple?: boolean;
  swatches?: boolean;
  disabled?: boolean;
  counts?: Readonly<Record<string, number>>;
  onToggle: (option: string) => void;
}) {
  return (
    <div aria-label={label} className="option-list" role="group">
      {options.map((option, index) => {
        const isSelected = selected.includes(option);
        return (
          <label className={isSelected ? "option selected" : "option"} key={option}>
            <input
              checked={isSelected}
              disabled={disabled}
              name={multiple ? undefined : name}
              onChange={() => onToggle(option)}
              type={multiple ? "checkbox" : "radio"}
            />
            {swatches ? (
              <span aria-hidden="true" className="option-swatch" style={{ background: categoryColor(index) }} />
            ) : (
              <span aria-hidden="true" className="option-check" />
            )}
            <span className="option-name">{option}</span>
            {counts && counts[option] ? (
              <span aria-hidden="true" className="option-count" title="本图中的数量">
                ×{counts[option]}
              </span>
            ) : null}
            {index < 9 && <kbd aria-hidden="true">{index + 1}</kbd>}
          </label>
        );
      })}
    </div>
  );
}

export type SegmentOption<T extends string> = Readonly<{ value: T; label: string; key?: string }>;

export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly SegmentOption<T>[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div aria-label={label} className="segmented" role="group">
      {options.map((option) => (
        <button
          aria-pressed={option.value === value}
          key={option.value}
          onClick={() => onChange(option.value)}
          type="button"
        >
          <span>{option.label}</span>
          {option.key && <kbd aria-hidden="true">{option.key}</kbd>}
        </button>
      ))}
    </div>
  );
}

export function RangeField({
  label,
  min,
  max,
  value,
  unit = "",
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  value: number;
  unit?: string;
  onChange: (value: number) => void;
}) {
  return (
    <label className="range-field">
      <span className="range-label">{label}</span>
      <input
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        type="range"
        value={value}
      />
      <output>
        {value}
        {unit}
      </output>
    </label>
  );
}

export function SubmitBar({
  disabled,
  submitting,
  hint,
  error,
  onSubmit,
  children,
}: {
  disabled: boolean;
  submitting: boolean;
  /** Why the button is disabled, shown only while it is. */
  hint?: string;
  error?: string;
  onSubmit: () => void;
  /** Extra controls in the error box (e.g. "刷新队列"). */
  children?: ReactNode;
}) {
  return (
    <div className="submit-bar">
      {error && (
        <div className="submit-error" role="alert">
          <span>保存失败：{error}</span>
          <div>
            <button disabled={submitting} onClick={onSubmit} type="button">
              重试保存
            </button>
            {children}
          </div>
        </div>
      )}
      {disabled && !submitting && hint && <p className="submit-hint-inline">{hint}</p>}
      <button className="primary-button" disabled={disabled || submitting} onClick={onSubmit} type="button">
        <span>{submitting ? "正在保存…" : "保存并继续"}</span>
        <kbd aria-hidden="true">{MOD_LABEL} ↵</kbd>
      </button>
    </div>
  );
}

export function CompleteState({
  projectId,
  title,
  description,
}: {
  projectId: string;
  title: string;
  description: string;
}) {
  return (
    <section className="state-panel review-complete">
      <p className="eyebrow">Queue complete</p>
      <h1>{title}</h1>
      <p>{description}</p>
      <Link className="text-link" to={`/projects/${projectId}`}>
        返回项目详情
      </Link>
    </section>
  );
}
