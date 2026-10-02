import type { ReactNode } from "react";
import { Link } from "wouter";

import type { Progress } from "../../project-meta";
import { categoryColor } from "./palette";
import { MOD_LABEL, type Hotkey } from "./useHotkeys";
import "./workspace.css";

export type ShortcutHint = Readonly<{ display: string; description: string }>;

/** ImageCanvas's built-in navigation, listed on every canvas page. */
export const CANVAS_HINTS: readonly ShortcutHint[] = [
  { display: "滚轮", description: "缩放" },
  { display: "空格 + 拖动", description: "平移画布" },
  { display: "0", description: "适应窗口（画布聚焦时）" },
];

/**
 * The full-viewport frame every annotation page shares: a slim bar with
 * where-am-I and progress, the image/canvas filling the rest, and a side
 * panel whose footer keeps the save button in place however long the
 * panel's content gets.
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
}) {
  const percent = progress && progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0;
  const shortcuts = [...hotkeys, ...hints];
  return (
    <section className="workspace">
      <header className="workspace-bar">
        <div className="workspace-where">
          <Link className="workspace-back" to={`/projects/${projectId}`}>
            ← {projectName}
          </Link>
          <h1>{title}</h1>
          {fileName && (
            <span className="workspace-file" title={fileName}>
              {fileName}
            </span>
          )}
        </div>
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
      </header>
      <div className="workspace-body">
        <div className="workspace-stage">{stage}</div>
        <aside className="workspace-panel" aria-label="标注面板">
          <div className="workspace-panel-content">
            {panel}
            {shortcuts.length > 0 && (
              <details className="shortcut-list">
                <summary>快捷键</summary>
                <dl>
                  {shortcuts.map((shortcut) => (
                    <div key={`${shortcut.display}-${shortcut.description}`}>
                      <dt>
                        <kbd>{shortcut.display}</kbd>
                      </dt>
                      <dd>{shortcut.description}</dd>
                    </div>
                  ))}
                </dl>
              </details>
            )}
          </div>
          <div className="workspace-panel-footer">{footer}</div>
        </aside>
      </div>
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
