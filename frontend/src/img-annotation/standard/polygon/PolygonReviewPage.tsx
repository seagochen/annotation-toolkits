import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../../api/client";
import { ErrorState, LoadingState } from "../../../components/AsyncState";
import {
  ImageCanvas,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Point,
  type Viewport,
} from "../../common/image-canvas";
import {
  beginCreate,
  beginMoveOrResize,
  cancelDrag,
  createBoxToolState,
  createBoxes,
  deleteBox,
  endDrag,
  hitTestBoxes,
  hitTestHandle,
  previewCreateRect,
  setCategory as setBoxCategory,
  updateDrag,
  type Box,
  type BoxToolState,
} from "../../common/image-canvas/box-tool";
import {
  MIN_POLYGON_POINTS,
  beginOrExtendDraft,
  cancelDraft,
  clampPoint,
  closeDraft,
  createPolygonToolState,
  createPolygons,
  deleteVertex,
  dragVertexTo,
  endVertexDrag,
  hitTestEdge,
  hitTestVertex,
  pointerDownEdit,
  relabelPolygon,
  removePolygon,
  selectPolygon,
  setCategory,
  undoLastPoint,
  type Polygon,
  type PolygonToolState,
} from "../../common/image-canvas/polygon-tool";
import {
  createKeyPoints,
  eraseBoxes,
  erasePoints,
  erasePolygons,
  hitTestKeyPoint,
  type KeyPoint,
} from "../../common/image-canvas/shape-eraser";
import {
  CANVAS_HINTS,
  ClassLayers,
  CompleteState,
  PanelSection,
  RangeField,
  Segmented,
  SubmitBar,
  TaskWorkspace,
  type LayerRow,
} from "../../common/workspace/TaskWorkspace";
import { categoryColor } from "../../common/workspace/palette";
import {
  BoxIcon,
  HandIcon,
  KeyPointIcon,
  PolygonIcon,
  SelectIcon,
  ShapeEraserIcon,
} from "../../common/workspace/tool-icons";
import { useEditHistory, useRecordChanges } from "../../common/workspace/useEditHistory";
import {
  DIGIT_KEYS,
  MOD_LABEL,
  REDO_KEYS,
  SAVE_KEYS,
  UNDO_KEYS,
  useHotkeys,
  type Hotkey,
} from "../../common/workspace/useHotkeys";
import { summaryProgress } from "../../../project-meta";
import { ImageStrip } from "../../common/ImageStrip";
import { useImageSize } from "../../common/useImageSize";
import { itemText, summaryStrings, useResetOnItem, useTaskQueue, type QueueItem } from "../../common/useTaskQueue";

const HANDLE_SCREEN_SIZE = 8;
const HIT_SCREEN_TOLERANCE = 7;
const MIN_BOX_SCREEN_SIZE = 3;
const POINT_SCREEN_RADIUS = 5;
const MIN_ERASER = 4;
const MAX_ERASER = 80;

type Mode = "edit" | "pan" | "draw" | "box" | "point" | "erase";
type StoredPolygon = { category: string; points: [number, number][] };
type StoredBox = { category: string; x: number; y: number; width: number; height: number };
type StoredPoint = { category: string; x: number; y: number };

/** What undo/redo restores: the three shape lists of the item. */
type Shapes = Readonly<{ polygons: readonly Polygon[]; boxes: readonly Box[]; points: readonly KeyPoint[] }>;

function sameShapes(a: Shapes, b: Shapes): boolean {
  return a.polygons === b.polygons && a.boxes === b.boxes && a.points === b.points;
}

const SOURCE_LABELS: Record<string, string> = {
  annotation: "已提交的结果",
  prelabel: "COCO 预标",
  none: "空白",
};

function storedList<T>(item: QueueItem, key: string): T[] {
  const value = item[key];
  return Array.isArray(value) ? (value as T[]) : [];
}

/** An item and each new revision of it start from what the server sent. */
function polygonItemKey(item: QueueItem): string {
  const revision = typeof item.revision === "number" ? item.revision : 0;
  return `${itemText(item, "item_id")}:${revision}:${itemText(item, "source")}`;
}

function storedSize(item: QueueItem): { width: number; height: number } | null {
  const size = item.image_size as { width?: unknown; height?: unknown } | null | undefined;
  return size && typeof size.width === "number" && typeof size.height === "number"
    ? { width: size.width, height: size.height }
    : null;
}

/** Two decimals is far below a pixel and keeps the JSON readable. */
function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/**
 * A box rounded to two decimals that still lies inside the image: rounding
 * both edges can push the far edge past the border by a hundredth (or a
 * floating-point ulp), which the server rejects.
 */
function roundBox(box: Box, size: { width: number; height: number }) {
  const x = round(box.x);
  const y = round(box.y);
  let width = round(Math.min(box.x + box.width, size.width) - x);
  let height = round(Math.min(box.y + box.height, size.height) - y);
  if (x + width > size.width) width = round(width - 0.01);
  if (y + height > size.height) height = round(height - 0.01);
  return { category: box.category, x, y, width, height };
}

function polygonArea(points: readonly Point[]): number {
  let total = 0;
  points.forEach((point, index) => {
    const next = points[(index + 1) % points.length];
    total += point.x * next.y - next.x * point.y;
  });
  return Math.abs(total) / 2;
}

function strokePath(context: CanvasRenderingContext2D, points: readonly Point[], close: boolean) {
  context.beginPath();
  points.forEach((point, index) => (index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y)));
  if (close) context.closePath();
}

export function PolygonReviewPage() {
  const { projectId = "" } = useParams();
  const [tool, setTool] = useState<PolygonToolState>(createPolygonToolState());
  const [boxTool, setBoxTool] = useState<BoxToolState>(createBoxToolState());
  const [keyPoints, setKeyPoints] = useState<readonly KeyPoint[]>([]);
  const [selectedPointId, setSelectedPointId] = useState<string | null>(null);
  const [pointDragId, setPointDragId] = useState<string | null>(null);
  const [mode, setMode] = useState<Mode>("edit");
  // The category picked in the panel: the canvas shows its shapes' vertices and dims the rest.
  const [focused, setFocused] = useState<string | null>(null);
  const [eraserRadius, setEraserRadius] = useState(16);
  const [erasing, setErasing] = useState(false);
  const erasingRef = useRef(false);
  // Last pointer position over the image: the eraser outline and box preview follow it.
  const [hover, setHover] = useState<Point | null>(null);
  const [notice, setNotice] = useState("");
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });

  const queue = useTaskQueue(projectId, {
    taskType: "polygon",
    wrongType: "该项目不是多边形标注任务。",
  });
  const { ready, item, submitting, submitError, view } = queue;
  const categories = useMemo(
    () => (ready ? summaryStrings(ready.project, "categories") : []),
    [ready],
  );
  const imagePath = item ? itemText(item, "image_path") : undefined;
  const imageUrl = imagePath ? projectFileUrl(projectId, imagePath) : undefined;
  const imageSize = useImageSize(imageUrl);
  const revision = item && typeof item.revision === "number" ? item.revision : 0;
  const itemKey = item ? polygonItemKey(item) : "";

  // Each item (and each new revision of it) starts from what the server sent;
  // `item` changes identity on every queue read, the key says when it is really new.
  useResetOnItem(queue, polygonItemKey, (shown) => {
    if (!shown) return;
    const polygons = createPolygons(
      storedList<StoredPolygon>(shown, "polygons").map((polygon) => ({
        category: polygon.category,
        points: polygon.points.map(([x, y]) => ({ x, y })),
      })),
    );
    setTool((current) => createPolygonToolState(polygons, current.category || categories[0] || ""));
    setBoxTool(createBoxToolState(createBoxes(storedList<StoredBox>(shown, "boxes"))));
    setKeyPoints(createKeyPoints(storedList<StoredPoint>(shown, "points")));
    setSelectedPointId(null);
    setPointDragId(null);
    erasingRef.current = false;
    setErasing(false);
    if (mode === "draw") setMode("edit");
    setNotice("");
  });

  const shapes: Shapes = useMemo(
    () => ({ polygons: tool.polygons, boxes: boxTool.boxes, points: keyPoints }),
    [tool.polygons, boxTool.boxes, keyPoints],
  );
  const history = useEditHistory<Shapes>();
  // A drag (vertex, box, point) or an eraser stroke is one step.
  const editing = Boolean(tool.drag || boxTool.drag || pointDragId || erasing);
  const markApplied = useRecordChanges(history, editing ? null : shapes, itemKey, sameShapes);

  // A browsed position that no longer exists (fewer results than before) falls back to the first.
  const { browse } = queue;
  const pastEnd = Boolean(ready && !item && view.status === "annotated" && view.offset > 0);
  useEffect(() => {
    if (pastEnd) void browse({ status: "annotated", offset: 0 });
  }, [pastEnd, browse]);

  const knownSize = item ? storedSize(item) : null;
  const sizeMismatch =
    imageSize && knownSize && (imageSize.width !== knownSize.width || imageSize.height !== knownSize.height);

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "shapes",
      render(context, frame) {
        const scale = frame.viewport.scale;
        const handle = HANDLE_SCREEN_SIZE / scale;
        const square = (point: Point, size: number, fill: string, stroke: string) => {
          context.fillStyle = fill;
          context.strokeStyle = stroke;
          context.lineWidth = 1.5 / scale;
          context.fillRect(point.x - size / 2, point.y - size / 2, size, size);
          context.strokeRect(point.x - size / 2, point.y - size / 2, size, size);
        };

        const dim = (category: string, selected: boolean) => {
          context.globalAlpha = focused !== null && category !== focused && !selected ? 0.35 : 1;
        };

        for (const polygon of tool.polygons) {
          const selected = polygon.id === tool.selectedId;
          const color = categoryColor(categories.indexOf(polygon.category));
          dim(polygon.category, selected);
          strokePath(context, polygon.points, true);
          context.fillStyle = `${color}${selected ? "55" : "33"}`;
          context.fill();
          context.lineWidth = (selected ? 3 : 2) / scale;
          context.strokeStyle = color;
          context.stroke();
          if (!selected) {
            if (polygon.category === focused) {
              for (const point of polygon.points) square(point, handle * 0.75, "#ffffff", color);
            }
            continue;
          }
          polygon.points.forEach((point, index) => {
            const active = index === tool.selectedVertex;
            square(point, active ? handle * 1.4 : handle, active ? color : "#ffffff", color);
          });
        }

        for (const box of boxTool.boxes) {
          const selected = box.id === boxTool.selectedId;
          const color = categoryColor(categories.indexOf(box.category));
          dim(box.category, selected);
          if (selected) {
            context.fillStyle = `${color}22`;
            context.fillRect(box.x, box.y, box.width, box.height);
          }
          context.lineWidth = (selected ? 3 : 2) / scale;
          context.strokeStyle = color;
          context.strokeRect(box.x, box.y, box.width, box.height);
          const right = box.x + box.width;
          const bottom = box.y + box.height;
          if (!selected) {
            if (box.category === focused) {
              for (const corner of [
                { x: box.x, y: box.y }, { x: right, y: box.y }, { x: right, y: bottom }, { x: box.x, y: bottom },
              ]) {
                square(corner, handle * 0.75, "#ffffff", color);
              }
            }
            continue;
          }
          const midX = box.x + box.width / 2;
          const midY = box.y + box.height / 2;
          for (const corner of [
            { x: box.x, y: box.y }, { x: midX, y: box.y }, { x: right, y: box.y }, { x: right, y: midY },
            { x: right, y: bottom }, { x: midX, y: bottom }, { x: box.x, y: bottom }, { x: box.x, y: midY },
          ]) {
            square(corner, handle, "#ffffff", color);
          }
        }

        for (const point of keyPoints) {
          const selected = point.id === selectedPointId;
          const color = categoryColor(categories.indexOf(point.category));
          dim(point.category, selected);
          const radius = (selected ? POINT_SCREEN_RADIUS + 2 : POINT_SCREEN_RADIUS) / scale;
          context.beginPath();
          context.arc(point.x, point.y, radius, 0, Math.PI * 2);
          context.fillStyle = color;
          context.fill();
          context.lineWidth = (selected ? 3 : 1.5) / scale;
          context.strokeStyle = "#ffffff";
          context.stroke();
        }
        context.globalAlpha = 1;

        const draftColor = categoryColor(categories.indexOf(tool.category));
        if (tool.draft?.length) {
          context.setLineDash([6 / scale, 4 / scale]);
          context.lineWidth = 2 / scale;
          context.strokeStyle = draftColor;
          strokePath(context, tool.draft, false);
          context.stroke();
          context.setLineDash([]);
          for (const point of tool.draft) {
            context.beginPath();
            context.arc(point.x, point.y, 3 / scale, 0, Math.PI * 2);
            context.fillStyle = draftColor;
            context.fill();
          }
        }
        const preview = mode === "box" && hover ? previewCreateRect(boxTool, hover) : null;
        if (preview) {
          context.setLineDash([6 / scale, 4 / scale]);
          context.lineWidth = 2 / scale;
          context.strokeStyle = draftColor;
          context.strokeRect(preview.x, preview.y, preview.width, preview.height);
          context.setLineDash([]);
        }
        if (mode === "erase" && hover) {
          context.beginPath();
          context.arc(hover.x, hover.y, eraserRadius / scale, 0, Math.PI * 2);
          context.fillStyle = "rgba(255, 255, 255, 0.18)";
          context.fill();
          context.setLineDash([4 / scale, 3 / scale]);
          context.lineWidth = 1.5 / scale;
          context.strokeStyle = "#ffffff";
          context.stroke();
          context.setLineDash([]);
        }
      },
    }),
    [boxTool, categories, eraserRadius, focused, hover, keyPoints, mode, selectedPointId, tool],
  );

  /** Keep only one kind of shape selected: the one being picked now. */
  function selectOnly(kind: "polygon" | "box" | "point" | null) {
    if (kind !== "polygon") setTool((current) => (current.selectedId ? selectPolygon(current, null) : current));
    if (kind !== "box") setBoxTool((current) => (current.selectedId ? { ...current, selectedId: null } : current));
    if (kind !== "point") setSelectedPointId(null);
  }

  function erase(center: Point) {
    const radius = eraserRadius / viewportRef.current.scale;
    // Functional updates: several pointer moves can arrive between renders.
    setKeyPoints((current) => erasePoints(current, center, radius));
    setBoxTool((current) => {
      const boxes = eraseBoxes(current.boxes, center, radius);
      if (boxes === current.boxes) return current;
      return { ...current, boxes, selectedId: boxes.some((box) => box.id === current.selectedId) ? current.selectedId : null };
    });
    setTool((current) => {
      const polygons = erasePolygons(current.polygons, center, radius);
      if (polygons === current.polygons) return current;
      const selected = polygons.find((polygon) => polygon.id === current.selectedId);
      const unchanged = selected && current.polygons.includes(selected);
      return {
        ...current,
        polygons,
        selectedId: selected ? current.selectedId : null,
        selectedVertex: unchanged ? current.selectedVertex : null,
        drag: null,
      };
    });
  }

  function editPointerDown(event: ImageCanvasPointerEvent, tolerance: number, handleSize: number) {
    const at = event.image;
    const pointHit = hitTestKeyPoint(keyPoints, at, tolerance);
    if (pointHit) {
      selectOnly("point");
      setSelectedPointId(pointHit);
      setPointDragId(pointHit);
      return;
    }
    const selectedBox = boxTool.boxes.find((box) => box.id === boxTool.selectedId);
    if (selectedBox && hitTestHandle(selectedBox, at, handleSize)) {
      selectOnly("box");
      setBoxTool((current) => beginMoveOrResize(current, at, handleSize));
      return;
    }
    const selectedPolygon = tool.polygons.find((polygon) => polygon.id === tool.selectedId);
    const onPolygonHandle =
      hitTestVertex(tool, at, tolerance) || (selectedPolygon && hitTestEdge(selectedPolygon, at, tolerance));
    // Boxes are drawn above polygons, so a box takes the click unless it is on a vertex or edge handle.
    if (!onPolygonHandle && hitTestBoxes(boxTool.boxes, at)) {
      selectOnly("box");
      setBoxTool((current) => beginMoveOrResize(current, at, handleSize));
      return;
    }
    selectOnly("polygon");
    setTool((current) => pointerDownEdit(current, at, tolerance));
  }

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!imageSize) return;
    const scale = viewportRef.current.scale;
    const tolerance = HIT_SCREEN_TOLERANCE / scale;
    const handleSize = HANDLE_SCREEN_SIZE / scale;
    const point = clampPoint(event.image, imageSize);
    const { phase } = event;

    if (mode === "draw") {
      if (phase === "down") setTool((current) => beginOrExtendDraft(current, point, tolerance));
      return;
    }
    if (mode === "box") {
      setHover(point);
      if (phase === "down") {
        selectOnly(null);
        setBoxTool((current) => beginCreate(current, point, tool.category));
      } else if (phase === "up") {
        setBoxTool((current) => endDrag(current, point, imageSize, MIN_BOX_SCREEN_SIZE / scale));
      } else if (phase === "cancel") {
        setBoxTool(cancelDrag);
      }
      return;
    }
    if (mode === "point") {
      if (phase !== "down") return;
      const [created] = createKeyPoints([{ category: tool.category, ...point }]);
      selectOnly("point");
      setKeyPoints((current) => [...current, created]);
      setSelectedPointId(created.id);
      return;
    }
    if (mode === "erase") {
      setHover(event.image);
      if (phase === "down") {
        erasingRef.current = true;
        setErasing(true);
        erase(event.image);
      } else if (phase === "move" && erasingRef.current) {
        erase(event.image);
      } else if (phase === "up" || phase === "cancel") {
        erasingRef.current = false;
        setErasing(false);
      }
      return;
    }

    // Edit (select) mode.
    if (phase === "down") {
      setNotice("");
      editPointerDown(event, tolerance, handleSize);
    } else if (phase === "move") {
      if (pointDragId) {
        setKeyPoints((current) =>
          current.map((candidate) => (candidate.id === pointDragId ? { ...candidate, ...point } : candidate)),
        );
      }
      setTool((current) => (current.drag ? dragVertexTo(current, event.image, imageSize) : current));
      setBoxTool((current) => (current.drag ? updateDrag(current, event.image, imageSize) : current));
    } else {
      setPointDragId(null);
      setTool(endVertexDrag);
      setBoxTool(cancelDrag);
    }
  }

  const selectedPolygon = tool.polygons.find((polygon) => polygon.id === tool.selectedId);
  const selectedBox = boxTool.boxes.find((box) => box.id === boxTool.selectedId);
  const selectedPoint = keyPoints.find((point) => point.id === selectedPointId);
  const selectedShape = selectedPoint ?? selectedBox ?? selectedPolygon;

  /** Relabel the selected shape; new shapes get the category too. */
  function relabel(next: string) {
    setTool((current) => setCategory(current, next));
    if (selectedPoint) {
      setKeyPoints((current) => current.map((point) => (point.id === selectedPoint.id ? { ...point, category: next } : point)));
    } else if (selectedBox) {
      setBoxTool((current) => setBoxCategory(current, selectedBox.id, next));
    } else if (selectedPolygon && mode === "edit") {
      setTool((current) => relabelPolygon(current, selectedPolygon.id, next));
    }
  }

  /**
   * A category row of the panel: new shapes get that category and its
   * vertices are shown; pressing the shown category again hides them.
   */
  function pickCategory(next: string) {
    setTool((current) => setCategory(current, next));
    setFocused((current) => (current === next ? null : next));
    selectOnly(null);
  }

  function chooseMode(next: Mode) {
    setNotice("");
    setHover(null);
    if (next !== "draw") setTool(cancelDraft);
    if (next !== "edit" && next !== "pan") selectOnly(null);
    setBoxTool(cancelDrag);
    setMode(next);
  }

  function toggleDraw() {
    chooseMode(mode === "draw" ? "edit" : "draw");
  }

  function finishDraft() {
    setTool((current) => {
      if (!current.draft) return current;
      const closed = closeDraft(current);
      if (closed.polygons.length === current.polygons.length) return current;
      return selectPolygon(closed, closed.polygons[closed.polygons.length - 1].id);
    });
    setMode("edit");
  }

  function deleteSelection() {
    if (selectedPoint) {
      setKeyPoints((current) => current.filter((point) => point.id !== selectedPoint.id));
      setSelectedPointId(null);
      return;
    }
    if (selectedBox) {
      setBoxTool((current) => deleteBox(current, selectedBox.id));
      return;
    }
    if (!selectedPolygon) return;
    if (tool.selectedVertex !== null) {
      if (selectedPolygon.points.length <= MIN_POLYGON_POINTS) {
        setNotice(`多边形至少需要 ${MIN_POLYGON_POINTS} 个顶点；要去掉整个多边形，请先取消选中顶点或用列表中的 ×。`);
        return;
      }
      setTool((current) => deleteVertex(current, selectedPolygon.id, current.selectedVertex ?? -1));
      return;
    }
    setTool((current) => removePolygon(current, selectedPolygon.id));
  }

  const result = imageSize
    ? {
        image_size: imageSize,
        base_revision: revision,
        polygons: tool.polygons.map((polygon) => ({
          category: polygon.category,
          points: polygon.points.map((point) => [round(point.x), round(point.y)]),
        })),
        boxes: boxTool.boxes.map((box) => roundBox(box, imageSize)),
        points: keyPoints.map((point) => ({ category: point.category, x: round(point.x), y: round(point.y) })),
      }
    : undefined;

  async function submit() {
    if (!item || !result || submitting) return;
    if (tool.draft?.length) {
      setNotice("还有未闭合的多边形：按 Enter 闭合，或按 Esc 放弃后再保存。");
      return;
    }
    await queue.submit(itemText(item, "item_id"), result);
  }

  function restore(step: (current: Shapes) => Shapes | undefined) {
    const next = step(shapes);
    if (!next) return;
    markApplied(next);
    setNotice("");
    setTool((current) => ({ ...current, polygons: next.polygons, selectedId: null, selectedVertex: null, drag: null }));
    setBoxTool({ boxes: next.boxes, selectedId: null, drag: null });
    setKeyPoints(next.points);
    setSelectedPointId(null);
    setPointDragId(null);
  }
  function undo() {
    // While drawing, undo takes back the draft's last point first.
    if (tool.draft?.length) setTool(undoLastPoint);
    else restore(history.undo);
  }
  const redo = () => restore(history.redo);

  const resizeEraser = (delta: number) =>
    setEraserRadius((current) => Math.min(MAX_ERASER, Math.max(MIN_ERASER, current + delta)));

  const hotkeys: Hotkey[] = [
    {
      keys: DIGIT_KEYS,
      display: "1–9",
      description: "选择类别并显示其顶点（选中形状时改为该类别）",
      run: (key) => {
        const next = categories[Number(key) - 1];
        if (!next) return;
        if (selectedShape) {
          relabel(next);
        } else {
          setTool((current) => setCategory(current, next));
          setFocused(next);
        }
      },
    },
    { keys: ["v"], display: "V", description: "选择 / 编辑形状", run: () => chooseMode("edit") },
    { keys: ["h"], display: "H", description: "拖动画布", run: () => chooseMode("pan") },
    { keys: ["p"], display: "P", description: "绘制新多边形 / 回到编辑", run: toggleDraw },
    { keys: ["b"], display: "B", description: "画矩形框", run: () => chooseMode("box") },
    { keys: ["k"], display: "K", description: "放置关键点", run: () => chooseMode("point") },
    { keys: ["e"], display: "E", description: "橡皮（擦除关键点、多边形顶点与框）", run: () => chooseMode("erase") },
    { keys: ["["], display: "[", description: "缩小橡皮", repeat: true, run: () => resizeEraser(-2) },
    { keys: ["]"], display: "]", description: "放大橡皮", repeat: true, run: () => resizeEraser(2) },
    { keys: ["enter"], display: "Enter", description: "闭合正在绘制的多边形", run: finishDraft },
    {
      keys: ["backspace"],
      display: "Backspace",
      description: "绘制时撤销最后一点；编辑时同 Del",
      repeat: true,
      run: () => (mode === "draw" ? setTool(undoLastPoint) : deleteSelection()),
    },
    {
      keys: ["delete"],
      display: "Del",
      description: "删除选中的关键点、框或顶点（未选顶点时删除多边形）",
      run: deleteSelection,
    },
    {
      keys: ["escape"],
      display: "Esc",
      description: "放弃正在绘制的多边形 / 取消选中与类别高亮",
      run: () => {
        setNotice("");
        if (mode === "draw") {
          setTool(cancelDraft);
          setMode("edit");
        } else {
          setBoxTool(cancelDrag);
          selectOnly(null);
          setFocused(null);
        }
      },
    },
    { keys: UNDO_KEYS, display: `${MOD_LABEL} + Z`, description: "撤销（绘制时撤销一点）", run: undo },
    { keys: REDO_KEYS, display: `${MOD_LABEL} + Shift + Z`, description: "重做", run: redo },
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (queue.state.kind === "loading") return <LoadingState>正在读取多边形队列…</LoadingState>;
  if (queue.state.kind === "error") {
    return <ErrorState message={queue.state.message} onRetry={() => void queue.reload()} />;
  }
  if (!ready) return <LoadingState>正在读取多边形队列…</LoadingState>;

  const viewSwitch = (
    <Segmented
      label="队列"
      onChange={(status) => void queue.browse({ status, offset: 0 })}
      options={[
        { value: "pending", label: "待标注" },
        { value: "annotated", label: "已提交" },
      ]}
      value={view.status}
    />
  );

  if (!item) {
    if (pastEnd) return <LoadingState>正在读取多边形队列…</LoadingState>;
    return (
      <div className="page">
        <CompleteState
          description={
            view.status === "pending"
              ? "当前没有待标注图像，所有结果均已原子写入本地 JSON。已提交的结果仍可在“已提交”中修改。"
              : "还没有已提交的结果。"
          }
          projectId={projectId}
          title={view.status === "pending" ? "多边形标注已完成" : "没有已提交的结果"}
        />
        <div className="queue-switch-standalone">{viewSwitch}</div>
      </div>
    );
  }

  const source = itemText(item, "source");
  const browsing = view.status === "annotated";
  const dirty = history.canUndo || Boolean(tool.draft?.length);

  const layerRows: LayerRow[] = [
    ...tool.polygons.map((polygon, index) => ({
      key: polygon.id,
      category: polygon.category,
      label: `多边形 #${index + 1}`,
      meta: `${polygon.points.length} 点 · ${Math.round(polygonArea(polygon.points))} px²`,
      active: polygon.id === tool.selectedId,
      onSelect: () => {
        setMode("edit");
        selectOnly("polygon");
        setTool((current) => selectPolygon(cancelDraft(current), polygon.id));
      },
      onDelete: () => setTool((current) => removePolygon(current, polygon.id)),
    })),
    ...boxTool.boxes.map((box, index) => ({
      key: box.id,
      category: box.category,
      label: `框 #${index + 1}`,
      meta: `${Math.round(box.width)}×${Math.round(box.height)}`,
      active: box.id === boxTool.selectedId,
      onSelect: () => {
        setMode("edit");
        selectOnly("box");
        setBoxTool((current) => ({ ...current, selectedId: box.id }));
      },
      onDelete: () => setBoxTool((current) => deleteBox(current, box.id)),
    })),
    ...keyPoints.map((point, index) => ({
      key: point.id,
      category: point.category,
      label: `点 #${index + 1}`,
      meta: `(${Math.round(point.x)}, ${Math.round(point.y)})`,
      active: point.id === selectedPointId,
      onSelect: () => {
        setMode("edit");
        selectOnly("point");
        setSelectedPointId(point.id);
      },
      onDelete: () => {
        setKeyPoints((current) => current.filter((candidate) => candidate.id !== point.id));
        if (selectedPointId === point.id) setSelectedPointId(null);
      },
    })),
  ];

  const modeNote: Record<Mode, string> = {
    edit: selectedPoint
      ? "拖动关键点移动；Del 删除。"
      : selectedBox
        ? "拖动框移动，拖动控制点调整大小；Del 删除。"
        : selectedPolygon
          ? tool.selectedVertex !== null
            ? `已选中第 ${tool.selectedVertex + 1} 个顶点：拖动移动，Del 删除。`
            : "拖动顶点移动；点击边插入顶点；Del 删除该多边形。"
          : "点击形状选中它，然后编辑；点击左侧类别显示该类全部顶点。",
    pan: "拖动平移画布；按 V 回到编辑。",
    draw: `逐点单击勾勒一个「${tool.category}」多边形，点回起点或按 Enter 闭合；Backspace 撤销一点。`,
    box: `在图像上拖动，画一个「${tool.category}」框。`,
    point: `单击图像放置一个「${tool.category}」关键点。`,
    erase: "按住拖动擦除：范围内的关键点与多边形顶点被删除（多边形由剩下的顶点重新围成），碰到边框的框被删除。",
  };

  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!imageSize}
          error={submitError}
          onSubmit={() => void submit()}
          submitting={submitting}
        >
          <button className="button-secondary" onClick={() => void queue.reload()} type="button">
            重新载入
          </button>
        </SubmitBar>
      }
      hints={[
        { display: "拖动顶点", description: "移动顶点" },
        { display: "点击选中多边形的边", description: "在该处插入顶点并可拖动" },
        ...CANVAS_HINTS,
      ]}
      hotkeys={hotkeys}
      panel={
        <>
          <PanelSection title="队列">
            {viewSwitch}
            <p className="panel-note">
              {browsing
                ? `已提交 ${view.offset + 1} / ${ready.queue.total}，第 ${revision} 版。保存会生成新版本，旧版本保留在历史中。`
                : `初始内容：${SOURCE_LABELS[source] ?? source}。`}
            </p>
            {sizeMismatch && knownSize && (
              <p className="inline-error" role="alert">
                图片实际尺寸 {imageSize?.width}×{imageSize?.height} 与{source === "prelabel" ? "预标" : "已保存结果"}的
                {knownSize.width}×{knownSize.height} 不一致，保存会被拒绝；请检查预标文件的 width/height。
              </p>
            )}
          </PanelSection>
          <ClassLayers
            categories={categories}
            current={tool.category}
            emptyNote="还没有标注。该图没有目标时可直接保存。"
            focused={focused}
            label="标注类别"
            onPick={pickCategory}
            onRelabel={relabel}
            rows={layerRows}
          />
          <p className="panel-note">{modeNote[mode]}</p>
          {notice && (
            <p className="inline-error" role="alert">
              {notice}
            </p>
          )}
        </>
      }
      history={{ canUndo: dirty, canRedo: history.canRedo, undo, redo }}
      progress={summaryProgress(ready.project.summary, browsing ? undefined : ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      rawData={result}
      remaining={browsing ? Number(ready.project.summary.pending ?? 0) : ready.queue.total}
      stage={
        imageSize && imageUrl ? (
          <ImageCanvas
            alt={imagePath ?? ""}
            imageSize={imageSize}
            interactionMode={mode === "pan" ? "pan" : "tool"}
            layers={[layer]}
            onPointerEvent={handlePointer}
            onViewportChange={(next) => {
              viewportRef.current = next;
            }}
            src={imageUrl}
          />
        ) : (
          <LoadingState>正在读取图像尺寸…</LoadingState>
        )
      }
      strip={<ImageStrip dirty={dirty} projectId={projectId} queue={queue} revisable />}
      title="多边形标注"
      toolOptions={
        mode === "erase" ? (
          <>
            <RangeField
              label="橡皮半径"
              max={MAX_ERASER}
              min={MIN_ERASER}
              onChange={setEraserRadius}
              unit="px"
              value={eraserRadius}
            />
            <p className="panel-note">半径按屏幕像素计，缩放画布不改变擦除手感；[ / ] 调整。</p>
          </>
        ) : undefined
      }
      tools={[
        [
          { id: "edit", label: "选择", icon: <SelectIcon />, shortcut: "V", active: mode === "edit", onSelect: () => chooseMode("edit") },
          { id: "pan", label: "拖动画布", icon: <HandIcon />, shortcut: "H", active: mode === "pan", onSelect: () => chooseMode("pan") },
        ],
        [
          { id: "draw", label: "多边形", icon: <PolygonIcon />, shortcut: "P", active: mode === "draw", onSelect: () => chooseMode("draw") },
          { id: "box", label: "矩形框", icon: <BoxIcon />, shortcut: "B", active: mode === "box", onSelect: () => chooseMode("box") },
          { id: "point", label: "关键点", icon: <KeyPointIcon />, shortcut: "K", active: mode === "point", onSelect: () => chooseMode("point") },
        ],
        [{ id: "erase", label: "橡皮", icon: <ShapeEraserIcon />, shortcut: "E", active: mode === "erase", onSelect: () => chooseMode("erase") }],
      ]}
    />
  );
}
