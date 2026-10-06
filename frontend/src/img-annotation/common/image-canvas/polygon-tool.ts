import type { Point, Size } from "./geometry";

export type Polygon = Readonly<{ id: string; category: string; points: readonly Point[] }>;

/** The vertex a pointer grabbed: which polygon, which point. */
export type VertexRef = Readonly<{ polygonId: string; index: number }>;

export type PolygonToolState = Readonly<{
  polygons: readonly Polygon[];
  draft: readonly Point[] | null;
  category: string;
  /** The polygon being edited (vertex handles shown), if any. */
  selectedId: string | null;
  /** The selected polygon's highlighted vertex, if any (Delete removes it). */
  selectedVertex: number | null;
  /** A vertex being dragged; cleared on pointer up. */
  drag: VertexRef | null;
}>;

/** A polygon must keep at least this many vertices. */
export const MIN_POLYGON_POINTS = 3;

let nextId = 0;

function createId(): string {
  nextId += 1;
  return `polygon-${Date.now().toString(36)}-${nextId}`;
}

export function createPolygonToolState(
  polygons: readonly Polygon[] = [],
  category = "",
): PolygonToolState {
  return { polygons, draft: null, category, selectedId: null, selectedVertex: null, drag: null };
}

/** Polygons with fresh ids from stored `{category, points}` (prelabels, saved results). */
export function createPolygons(
  values: readonly Readonly<{ category: string; points: readonly Point[] }>[],
): Polygon[] {
  return values.map((value) => ({ id: createId(), category: value.category, points: value.points }));
}

export function beginOrExtendDraft(
  state: PolygonToolState,
  point: Point,
  closeDistance: number,
): PolygonToolState {
  if (!state.draft || state.draft.length === 0) {
    return { ...state, draft: [point] };
  }
  const first = state.draft[0];
  const closing =
    state.draft.length >= 3 &&
    Math.hypot(point.x - first.x, point.y - first.y) <= closeDistance;
  if (closing) return closeDraft(state);
  return { ...state, draft: [...state.draft, point] };
}

export function closeDraft(state: PolygonToolState): PolygonToolState {
  if (!state.draft || state.draft.length < 3) return { ...state, draft: null };
  const polygon: Polygon = { id: createId(), category: state.category, points: state.draft };
  return { ...state, polygons: [...state.polygons, polygon], draft: null };
}

export function cancelDraft(state: PolygonToolState): PolygonToolState {
  return { ...state, draft: null };
}

export function undoLastPoint(state: PolygonToolState): PolygonToolState {
  if (!state.draft || state.draft.length === 0) return state;
  const draft = state.draft.slice(0, -1);
  return { ...state, draft: draft.length ? draft : null };
}

export function removePolygon(state: PolygonToolState, id: string): PolygonToolState {
  const selected = state.selectedId === id;
  return {
    ...state,
    polygons: state.polygons.filter((polygon) => polygon.id !== id),
    selectedId: selected ? null : state.selectedId,
    selectedVertex: selected ? null : state.selectedVertex,
    drag: state.drag?.polygonId === id ? null : state.drag,
  };
}

export function setCategory(state: PolygonToolState, category: string): PolygonToolState {
  return { ...state, category };
}

export function clearPolygons(state: PolygonToolState): PolygonToolState {
  return { ...state, polygons: [], selectedId: null, selectedVertex: null, drag: null };
}

// ------------------------------------------------------------------ editing

/** Keep a point inside the image: `[0, width] × [0, height]`. */
export function clampPoint(point: Point, bounds: Size): Point {
  return {
    x: Math.min(bounds.width, Math.max(0, point.x)),
    y: Math.min(bounds.height, Math.max(0, point.y)),
  };
}

/** Even-odd rule; points exactly on an edge may land either way. */
export function pointInPolygon(points: readonly Point[], point: Point): boolean {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i, i += 1) {
    const a = points[i];
    const b = points[j];
    if (a.y > point.y !== b.y > point.y && point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x) {
      inside = !inside;
    }
  }
  return inside;
}

/** The topmost (last drawn) polygon containing a point. */
export function hitTestPolygon(polygons: readonly Polygon[], point: Point): string | null {
  for (let index = polygons.length - 1; index >= 0; index -= 1) {
    if (pointInPolygon(polygons[index].points, point)) return polygons[index].id;
  }
  return null;
}

/** The nearest vertex within `tolerance`, preferring the selected polygon. */
export function hitTestVertex(
  state: PolygonToolState,
  point: Point,
  tolerance: number,
): VertexRef | null {
  const ordered = [
    ...state.polygons.filter((polygon) => polygon.id === state.selectedId),
    ...[...state.polygons].reverse().filter((polygon) => polygon.id !== state.selectedId),
  ];
  for (const polygon of ordered) {
    let best: VertexRef | null = null;
    let bestDistance = tolerance;
    for (let index = 0; index < polygon.points.length; index += 1) {
      const vertex = polygon.points[index];
      const distance = Math.hypot(vertex.x - point.x, vertex.y - point.y);
      if (distance <= bestDistance) {
        best = { polygonId: polygon.id, index };
        bestDistance = distance;
      }
    }
    if (best) return best;
  }
  return null;
}

/**
 * The edge of `polygon` nearest to `point` within `tolerance`: inserting at
 * `index + 1` puts a new vertex on the edge from vertex `index` to the next.
 */
export function hitTestEdge(
  polygon: Polygon,
  point: Point,
  tolerance: number,
): { index: number; point: Point } | null {
  let best: { index: number; point: Point } | null = null;
  let bestDistance = tolerance;
  const { points } = polygon;
  for (let index = 0; index < points.length; index += 1) {
    const a = points[index];
    const b = points[(index + 1) % points.length];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSquared = dx * dx + dy * dy;
    const t = lengthSquared === 0 ? 0 : Math.max(0, Math.min(1, ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared));
    const projected = { x: a.x + t * dx, y: a.y + t * dy };
    const distance = Math.hypot(projected.x - point.x, projected.y - point.y);
    if (distance <= bestDistance) {
      best = { index, point: projected };
      bestDistance = distance;
    }
  }
  return best;
}

export function selectPolygon(state: PolygonToolState, id: string | null): PolygonToolState {
  return { ...state, selectedId: id, selectedVertex: null, drag: null };
}

function updatePoints(
  state: PolygonToolState,
  polygonId: string,
  change: (points: readonly Point[]) => readonly Point[],
): readonly Polygon[] {
  return state.polygons.map((polygon) =>
    polygon.id === polygonId ? { ...polygon, points: change(polygon.points) } : polygon,
  );
}

export function beginVertexDrag(state: PolygonToolState, vertex: VertexRef): PolygonToolState {
  return { ...state, selectedId: vertex.polygonId, selectedVertex: vertex.index, drag: vertex };
}

/** Move the dragged vertex, kept inside the image. */
export function dragVertexTo(state: PolygonToolState, point: Point, bounds: Size): PolygonToolState {
  const drag = state.drag;
  if (!drag) return state;
  const target = clampPoint(point, bounds);
  return {
    ...state,
    polygons: updatePoints(state, drag.polygonId, (points) =>
      points.map((vertex, index) => (index === drag.index ? target : vertex)),
    ),
  };
}

export function endVertexDrag(state: PolygonToolState): PolygonToolState {
  return state.drag ? { ...state, drag: null } : state;
}

/** Insert a vertex after `afterIndex` and select it. */
export function insertVertex(
  state: PolygonToolState,
  polygonId: string,
  afterIndex: number,
  point: Point,
): PolygonToolState {
  const polygon = state.polygons.find((candidate) => candidate.id === polygonId);
  if (!polygon || afterIndex < 0 || afterIndex >= polygon.points.length) return state;
  return {
    ...state,
    polygons: updatePoints(state, polygonId, (points) => [
      ...points.slice(0, afterIndex + 1),
      point,
      ...points.slice(afterIndex + 1),
    ]),
    selectedId: polygonId,
    selectedVertex: afterIndex + 1,
  };
}

/** Remove one vertex; a polygon never drops below `MIN_POLYGON_POINTS`. */
export function deleteVertex(state: PolygonToolState, polygonId: string, index: number): PolygonToolState {
  const polygon = state.polygons.find((candidate) => candidate.id === polygonId);
  if (!polygon || polygon.points.length <= MIN_POLYGON_POINTS || index < 0 || index >= polygon.points.length) {
    return state;
  }
  return {
    ...state,
    polygons: updatePoints(state, polygonId, (points) => points.filter((_, position) => position !== index)),
    selectedVertex: state.selectedId === polygonId ? null : state.selectedVertex,
    drag: null,
  };
}

export function relabelPolygon(state: PolygonToolState, id: string, category: string): PolygonToolState {
  return {
    ...state,
    polygons: state.polygons.map((polygon) => (polygon.id === id ? { ...polygon, category } : polygon)),
  };
}

/**
 * What a pointer-down does while editing (not drawing): grab a vertex (any
 * polygon, the selected one first); on the selected polygon's edge, insert a
 * vertex there and grab it; inside a polygon, select it; elsewhere, deselect.
 */
export function pointerDownEdit(state: PolygonToolState, point: Point, tolerance: number): PolygonToolState {
  const vertex = hitTestVertex(state, point, tolerance);
  if (vertex) return beginVertexDrag(state, vertex);
  const selected = state.polygons.find((polygon) => polygon.id === state.selectedId);
  const edge = selected ? hitTestEdge(selected, point, tolerance) : null;
  if (selected && edge) {
    const inserted = insertVertex(state, selected.id, edge.index, edge.point);
    return beginVertexDrag(inserted, { polygonId: selected.id, index: edge.index + 1 });
  }
  return selectPolygon(state, hitTestPolygon(state.polygons, point));
}
