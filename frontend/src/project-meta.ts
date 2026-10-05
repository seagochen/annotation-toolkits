/** Display names and entry points shared by the project list and detail pages. */

export const statusLabels: Record<string, string> = {
  missing: "数据缺失",
  empty: "等待数据",
  needs_mining: "等待候选挖掘",
  reviewing: "标注中",
  reviewed: "已完成",
  invalid: "配置错误",
};

export type TaskEntry = Readonly<{ label: string; path: string; action: string }>;

const TASK_ENTRIES = {
  reid: { label: "行人重识别", path: "review", action: "开始审核候选" },
  classification: { label: "分类", path: "classify", action: "开始分类" },
  captioning: { label: "描述 / 文本生成", path: "caption", action: "开始描述" },
  text_span: { label: "文本片段标注", path: "spans", action: "开始片段标注" },
  detection: { label: "目标检测", path: "detect", action: "开始目标检测" },
  segmentation: { label: "图像分割", path: "segment", action: "开始图像分割" },
  polygon: { label: "多边形标注", path: "polygon", action: "开始多边形标注" },
  depth: { label: "深度图修正", path: "depth", action: "开始深度图标注" },
} as const satisfies Record<string, TaskEntry>;

/** The task types the frontend has pages for; tasks/pages.ts must cover each. */
export type TaskType = keyof typeof TASK_ENTRIES;

/** Task type → label, route segment and entry text: the one place they are defined. */
export const taskEntries: Readonly<Record<string, TaskEntry>> = TASK_ENTRIES;

export function taskLabel(taskType: string): string {
  return taskEntries[taskType]?.label ?? taskType;
}

export type Progress = Readonly<{ done: number; total: number }>;

function count(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * Image tasks report `total`/`pending`; ReID reports `labelled`/`pending`.
 * `remaining` (the live queue size) wins over the summary's `pending`, which
 * was read once when the page loaded.
 */
export function summaryProgress(
  summary: Record<string, unknown>,
  remaining?: number,
): Progress | null {
  const pending = remaining ?? count(summary.pending);
  if (pending === null) return null;
  const labelled = count(summary.labelled);
  const total = count(summary.total) ?? (labelled !== null ? labelled + (count(summary.pending) ?? pending) : null);
  if (total === null) return null;
  return { done: Math.max(0, total - pending), total: Math.max(total, pending) };
}
