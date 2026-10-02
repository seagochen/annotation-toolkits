/** Display names and entry points shared by the project list and detail pages. */

export const statusLabels: Record<string, string> = {
  missing: "数据缺失",
  empty: "等待数据",
  needs_mining: "等待候选挖掘",
  reviewing: "标注中",
  reviewed: "已完成",
};

export type TaskEntry = Readonly<{ label: string; path: string; action: string }>;

export const taskEntries: Record<string, TaskEntry> = {
  reid: { label: "ReID 审核", path: "review", action: "开始审核候选" },
  classification: { label: "图像分类", path: "classify", action: "开始图像分类" },
  captioning: { label: "图像描述", path: "caption", action: "开始图像描述" },
  detection: { label: "目标检测", path: "detect", action: "开始目标检测" },
  segmentation: { label: "图像分割", path: "segment", action: "开始图像分割" },
  depth: { label: "深度图", path: "depth", action: "开始深度图标注" },
};

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
