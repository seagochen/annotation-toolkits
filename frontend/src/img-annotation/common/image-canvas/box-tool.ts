import type { Point, Size } from "./geometry";

export type Box = Readonly<{
  id: string;
  category: string;
  x: number;
  y: number;
  width: number;
  height: number;
}>;

export type BoxHandle = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";

type CreateDrag = Readonly<{ kind: "create"; start: Point; category: string }>;
type MoveDrag = Readonly<{ kind: "move"; id: string; start: Point; origin: Box }>;
type ResizeDrag = Readonly<{
  kind: "resize";
  id: string;
  handle: BoxHandle;
  origin: Box;
}>;
type Drag = CreateDrag | MoveDrag | ResizeDrag;

export type BoxToolState = Readonly<{
  boxes: readonly Box[];
  selectedId: string | null;
  drag: Drag | null;
}>;

let nextId = 0;

function createId(): string {
  nextId += 1;
  return `box-${Date.now().toString(36)}-${nextId}`;
}

export function createBoxToolState(boxes: readonly Box[] = []): BoxToolState {
  return { boxes, selectedId: null, drag: null };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function normalizeRect(a: Point, b: Point): { x: number; y: number; width: number; height: number } {
  const x = Math.min(a.x, b.x);
  const y = Math.min(a.y, b.y);
  return { x, y, width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) };
}

function clampBox(box: Box, bounds: Size): Box {
  const x = clamp(box.x, 0, Math.max(bounds.width - box.width, 0));
  const y = clamp(box.y, 0, Math.max(bounds.height - box.height, 0));
  const width = Math.min(box.width, bounds.width);
  const height = Math.min(box.height, bounds.height);
  return { ...box, x, y, width, height };
}

const HANDLE_POSITIONS: Record<BoxHandle, (box: Box) => Point> = {
  nw: (box) => ({ x: box.x, y: box.y }),
  n: (box) => ({ x: box.x + box.width / 2, y: box.y }),
  ne: (box) => ({ x: box.x + box.width, y: box.y }),
  e: (box) => ({ x: box.x + box.width, y: box.y + box.height / 2 }),
  se: (box) => ({ x: box.x + box.width, y: box.y + box.height }),
  s: (box) => ({ x: box.x + box.width / 2, y: box.y + box.height }),
  sw: (box) => ({ x: box.x, y: box.y + box.height }),
  w: (box) => ({ x: box.x, y: box.y + box.height / 2 }),
};

export function hitTestHandle(box: Box, point: Point, handleSize: number): BoxHandle | null {
  for (const handle of Object.keys(HANDLE_POSITIONS) as BoxHandle[]) {
    const position = HANDLE_POSITIONS[handle](box);
    if (Math.abs(position.x - point.x) <= handleSize && Math.abs(position.y - point.y) <= handleSize) {
      return handle;
    }
  }
  return null;
}

function pointInBox(box: Box, point: Point): boolean {
  return (
    point.x >= box.x &&
    point.x <= box.x + box.width &&
    point.y >= box.y &&
    point.y <= box.y + box.height
  );
}

export function hitTestBoxes(boxes: readonly Box[], point: Point): string | null {
  for (let index = boxes.length - 1; index >= 0; index -= 1) {
    if (pointInBox(boxes[index], point)) return boxes[index].id;
  }
  return null;
}

export function beginCreate(state: BoxToolState, start: Point, category: string): BoxToolState {
  return { ...state, selectedId: null, drag: { kind: "create", start, category } };
}

export function beginMoveOrResize(
  state: BoxToolState,
  point: Point,
  handleSize: number,
): BoxToolState {
  const selected = state.boxes.find((box) => box.id === state.selectedId);
  if (selected) {
    const handle = hitTestHandle(selected, point, handleSize);
    if (handle) {
      return { ...state, drag: { kind: "resize", id: selected.id, handle, origin: selected } };
    }
  }
  const hitId = hitTestBoxes(state.boxes, point);
  if (!hitId) return { ...state, selectedId: null, drag: null };
  const hitBox = state.boxes.find((box) => box.id === hitId)!;
  return {
    ...state,
    selectedId: hitId,
    drag: { kind: "move", id: hitId, start: point, origin: hitBox },
  };
}

function applyResize(origin: Box, handle: BoxHandle, point: Point): Box {
  const left = handle.includes("w") ? point.x : origin.x;
  const right = handle.includes("e") ? point.x : origin.x + origin.width;
  const top = handle.includes("n") ? point.y : origin.y;
  const bottom = handle.includes("s") ? point.y : origin.y + origin.height;
  const rect = normalizeRect({ x: left, y: top }, { x: right, y: bottom });
  return { ...origin, ...rect };
}

export function updateDrag(state: BoxToolState, point: Point, bounds: Size): BoxToolState {
  if (!state.drag) return state;
  if (state.drag.kind === "create") {
    return state;
  }
  if (state.drag.kind === "move") {
    const { id, start, origin } = state.drag;
    const delta = { x: point.x - start.x, y: point.y - start.y };
    const moved = clampBox({ ...origin, x: origin.x + delta.x, y: origin.y + delta.y }, bounds);
    return { ...state, boxes: state.boxes.map((box) => (box.id === id ? moved : box)) };
  }
  const { id, handle, origin } = state.drag;
  const resized = clampBox(applyResize(origin, handle, point), bounds);
  return { ...state, boxes: state.boxes.map((box) => (box.id === id ? resized : box)) };
}

export function previewCreateRect(
  state: BoxToolState,
  point: Point,
): { x: number; y: number; width: number; height: number } | null {
  if (!state.drag || state.drag.kind !== "create") return null;
  return normalizeRect(state.drag.start, point);
}

export function endDrag(state: BoxToolState, point: Point, bounds: Size, minSize = 1): BoxToolState {
  if (!state.drag) return state;
  if (state.drag.kind === "create") {
    const rect = clampBox(
      { id: createId(), category: state.drag.category, ...normalizeRect(state.drag.start, point) },
      bounds,
    );
    if (rect.width < minSize || rect.height < minSize) {
      return { ...state, drag: null };
    }
    return { boxes: [...state.boxes, rect], selectedId: rect.id, drag: null };
  }
  return { ...state, drag: null };
}

export function cancelDrag(state: BoxToolState): BoxToolState {
  if (!state.drag) return state;
  return { ...state, drag: null };
}

export function deleteBox(state: BoxToolState, id: string): BoxToolState {
  return {
    boxes: state.boxes.filter((box) => box.id !== id),
    selectedId: state.selectedId === id ? null : state.selectedId,
    drag: null,
  };
}

export function setCategory(state: BoxToolState, id: string, category: string): BoxToolState {
  return {
    ...state,
    boxes: state.boxes.map((box) => (box.id === id ? { ...box, category } : box)),
  };
}
