import type { Point } from "./geometry";

export type Polygon = Readonly<{ id: string; category: string; points: readonly Point[] }>;

export type PolygonToolState = Readonly<{
  polygons: readonly Polygon[];
  draft: readonly Point[] | null;
  category: string;
}>;

let nextId = 0;

function createId(): string {
  nextId += 1;
  return `polygon-${Date.now().toString(36)}-${nextId}`;
}

export function createPolygonToolState(
  polygons: readonly Polygon[] = [],
  category = "",
): PolygonToolState {
  return { polygons, draft: null, category };
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
  return { polygons: [...state.polygons, polygon], draft: null, category: state.category };
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
  return { ...state, polygons: state.polygons.filter((polygon) => polygon.id !== id) };
}

export function setCategory(state: PolygonToolState, category: string): PolygonToolState {
  return { ...state, category };
}

export function clearPolygons(state: PolygonToolState): PolygonToolState {
  return { ...state, polygons: [] };
}
