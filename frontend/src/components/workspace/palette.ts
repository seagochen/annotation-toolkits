/**
 * One colour per configured category, by position, shared by every place a
 * category is drawn (detection boxes, segmentation masks, the side-panel
 * swatches) so the annotator can read the canvas against the legend.
 * Hues are spread so neighbouring indices stay distinguishable on photos.
 */
const CATEGORY_COLORS = [
  "#22c55e",
  "#f97316",
  "#3b82f6",
  "#ec4899",
  "#a855f7",
  "#eab308",
  "#14b8a6",
  "#ef4444",
  "#84cc16",
  "#6366f1",
] as const;

/** `index` is the 0-based position in the project's category list. */
export function categoryColor(index: number): string {
  if (index < 0) return "#94a3b8";
  return CATEGORY_COLORS[index % CATEGORY_COLORS.length];
}

export function hexToRgb(hex: string): [number, number, number] {
  const value = parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}
