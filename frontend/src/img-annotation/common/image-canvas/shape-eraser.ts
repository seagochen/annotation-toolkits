import type { Box } from "./box-tool";
import type { Point } from "./geometry";
import { MIN_POLYGON_POINTS, type Polygon } from "./polygon-tool";

/** A single labelled point (COCO keypoint with one point). */
export type KeyPoint = Readonly<{ id: string; category: string; x: number; y: number }>;

export type Shapes = Readonly<{
  polygons: readonly Polygon[];
  boxes: readonly Box[];
  points: readonly KeyPoint[];
}>;

let nextId = 0;

/** Keypoints with fresh ids from stored `{category, x, y}` (prelabels, saved results). */
export function createKeyPoints(
  values: readonly Readonly<{ category: string; x: number; y: number }>[],
): KeyPoint[] {
  return values.map((value) => {
    nextId += 1;
    return { id: `point-${Date.now().toString(36)}-${nextId}`, category: value.category, x: value.x, y: value.y };
  });
}

/** The topmost (last placed) keypoint within `tolerance` of `point`. */
export function hitTestKeyPoint(points: readonly KeyPoint[], point: Point, tolerance: number): string | null {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const candidate = points[index];
    if (Math.hypot(candidate.x - point.x, candidate.y - point.y) <= tolerance) return candidate.id;
  }
  return null;
}

/** Distance from `point` to the outline of `box` (0 on it, inside or out). */
function distanceToOutline(box: Box, point: Point): number {
  const right = box.x + box.width;
  const bottom = box.y + box.height;
  const dx = Math.max(box.x - point.x, 0, point.x - right);
  const dy = Math.max(box.y - point.y, 0, point.y - bottom);
  if (dx > 0 || dy > 0) return Math.hypot(dx, dy);
  return Math.min(point.x - box.x, right - point.x, point.y - box.y, bottom - point.y);
}

function within(point: Point, center: Point, radius: number): boolean {
  return Math.hypot(point.x - center.x, point.y - center.y) <= radius;
}

/** Keypoints inside the circle removed; the same array when none is. */
export function erasePoints<K extends KeyPoint>(points: readonly K[], center: Point, radius: number): readonly K[] {
  const kept = points.filter((point) => !within(point, center, radius));
  return kept.length === points.length ? points : kept;
}

/** Boxes whose outline the circle touches removed; the same array when none is. */
export function eraseBoxes<B extends Box>(boxes: readonly B[], center: Point, radius: number): readonly B[] {
  const kept = boxes.filter((box) => distanceToOutline(box, center) > radius);
  return kept.length === boxes.length ? boxes : kept;
}

/**
 * Polygon vertices inside the circle removed, the remaining vertices (in
 * order) forming the polygon; a polygon left with fewer than
 * `MIN_POLYGON_POINTS` is removed. The same array when nothing changes.
 */
export function erasePolygons<P extends Polygon>(polygons: readonly P[], center: Point, radius: number): readonly P[] {
  let changed = false;
  const result: P[] = [];
  for (const polygon of polygons) {
    const kept = polygon.points.filter((point) => !within(point, center, radius));
    if (kept.length === polygon.points.length) {
      result.push(polygon);
      continue;
    }
    changed = true;
    if (kept.length >= MIN_POLYGON_POINTS) result.push({ ...polygon, points: kept });
  }
  return changed ? result : polygons;
}

/**
 * Erase everything the eraser circle covers:
 *
 * - a keypoint inside the circle is removed;
 * - polygon vertices inside the circle are removed and the remaining vertices,
 *   in their order, form the polygon; one left with fewer than
 *   `MIN_POLYGON_POINTS` vertices is removed;
 * - a box is removed when the circle touches its outline, so erasing a point
 *   or vertex inside a large box leaves the box alone.
 *
 * Unchanged lists keep their identity, and when nothing is erased the same
 * `shapes` object comes back, so callers can skip the update.
 */
export function eraseAt<S extends Shapes>(shapes: S, center: Point, radius: number): S {
  const polygons = erasePolygons(shapes.polygons, center, radius);
  const boxes = eraseBoxes(shapes.boxes, center, radius);
  const points = erasePoints(shapes.points, center, radius);
  if (polygons === shapes.polygons && boxes === shapes.boxes && points === shapes.points) return shapes;
  return { ...shapes, polygons, boxes, points };
}
