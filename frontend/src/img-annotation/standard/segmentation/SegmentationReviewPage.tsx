import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../../api/client";
import { LoadingState } from "../../../components/AsyncState";
import {
  CANVAS_HINTS,
  OptionList,
  PanelSection,
  RangeField,
  SubmitBar,
  TaskWorkspace,
} from "../../common/workspace/TaskWorkspace";
import { categoryColor, hexToRgb } from "../../common/workspace/palette";
import { BrushIcon, EraserIcon, HandIcon, PolygonIcon } from "../../common/workspace/tool-icons";
import { useEditHistory } from "../../common/workspace/useEditHistory";
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
import {
  ImageCanvas,
  drawRaster,
  useRasterBrush,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Viewport,
} from "../../common/image-canvas";
import {
  beginOrExtendDraft,
  cancelDraft,
  closeDraft,
  createPolygonToolState,
  setCategory as setPolygonCategory,
  undoLastPoint,
  type PolygonToolState,
} from "../../common/image-canvas/polygon-tool";
import {
  cloneRasterBuffer,
  createRasterBuffer,
  fillPolygon,
  toBase64,
  type RasterBuffer,
} from "../../common/image-canvas/raster-buffer";
import { ImageStrip } from "../../common/ImageStrip";
import { useImageSize } from "../../common/useImageSize";
import { QueueFallback, itemText, summaryStrings, useResetOnItem, useTaskQueue } from "../../common/useTaskQueue";

const HANDLE_SCREEN_SIZE = 8;
const MIN_RADIUS = 1;
const MAX_RADIUS = 100;

type Tool = "pan" | "brush" | "erase" | "polygon";
// Each undo step keeps a whole copy of the mask.
const HISTORY_LIMIT = 20;

function categoryIndex(categories: readonly string[], category: string): number {
  const index = categories.indexOf(category);
  return index === -1 ? 0 : index + 1;
}

// Mask value 0 is background; value N is the (N-1)th configured category.
function colorFor(index: number): readonly [number, number, number, number] {
  if (index === 0) return [0, 0, 0, 0];
  const [r, g, b] = hexToRgb(categoryColor(index - 1));
  return [r, g, b, 140];
}

export function SegmentationReviewPage() {
  const { projectId = "" } = useParams();
  const [tool, setTool] = useState<Tool>("brush");
  const [radius, setRadius] = useState(12);
  const [category, setCategory] = useState("");
  const [raster, setRaster] = useState<RasterBuffer | null>(null);
  const [polygonTool, setPolygonTool] = useState<PolygonToolState>(createPolygonToolState());
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });
  const paint = useRasterBrush(setRaster);

  const queue = useTaskQueue(projectId, {
    taskType: "segmentation",
    wrongType: "该项目不是图像分割任务。",
    onLoad: (project) => {
      const first = summaryStrings(project, "categories")[0] ?? "";
      setCategory(first);
      setPolygonTool(createPolygonToolState([], first));
    },
  });
  const { ready, item, submitting, submitError } = queue;
  const history = useEditHistory<RasterBuffer>(HISTORY_LIMIT);
  useResetOnItem(item ? itemText(item, "item_id") : "", () => {
    setRaster(null);
    setPolygonTool((current) => createPolygonToolState([], current.category));
    history.clear();
  });
  const imagePath = item ? itemText(item, "image_path") : undefined;
  const imageUrl = imagePath ? projectFileUrl(projectId, imagePath) : undefined;
  const imageSize = useImageSize(imageUrl);
  const categories = useMemo(
    () => (ready ? summaryStrings(ready.project, "categories") : []),
    [ready],
  );

  useEffect(() => {
    if (imageSize && !raster) {
      setRaster(createRasterBuffer(imageSize.width, imageSize.height, 0));
    }
  }, [imageSize, raster]);

  const activeIndex = tool === "erase" ? 0 : categoryIndex(categories, category);

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "segmentation-mask",
      render(context, frame) {
        if (!raster) return;
        drawRaster(context, raster, colorFor);

        const scale = frame.viewport.scale;
        for (const polygon of polygonTool.polygons) {
          drawPolygonOutline(context, polygon.points, scale, "#ffffff", false);
        }
        if (polygonTool.draft) {
          drawPolygonOutline(
            context,
            polygonTool.draft,
            scale,
            categoryColor(categories.indexOf(polygonTool.category)),
            true,
          );
        }
      },
    }),
    [categories, raster, polygonTool],
  );

  /**
   * Apply a polygon-tool transition; when it closed a polygon, fill that
   * polygon into the mask with its category.
   */
  function updatePolygons(change: (current: PolygonToolState) => PolygonToolState) {
    const next = change(polygonTool);
    setPolygonTool(next);
    if (raster && next.polygons.length > polygonTool.polygons.length) {
      const finished = next.polygons[next.polygons.length - 1];
      history.record(cloneRasterBuffer(raster));
      setRaster(
        (buffer) =>
          buffer && {
            ...fillPolygon(buffer, finished.points, categoryIndex(categories, finished.category)),
          },
      );
    }
  }

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!raster) return;
    if (tool === "polygon") {
      if (event.phase === "down") {
        const closeDistance = HANDLE_SCREEN_SIZE / viewportRef.current.scale;
        updatePolygons((current) => beginOrExtendDraft(current, event.image, closeDistance));
      }
      return;
    }
    if (event.phase === "down") history.record(cloneRasterBuffer(raster));
    paint(event, radius, () => activeIndex);
  }

  function restore(step: (current: RasterBuffer) => RasterBuffer | undefined) {
    if (!raster) return;
    const previous = step(cloneRasterBuffer(raster));
    if (previous) setRaster(previous);
  }
  function undo() {
    // While outlining a polygon, undo takes back its last point first.
    if (polygonTool.draft?.length) setPolygonTool(undoLastPoint);
    else restore(history.undo);
  }
  const redo = () => restore(history.redo);

  async function submit() {
    if (!item || !raster) return;
    await queue.submit(itemText(item, "item_id"), {
      image_size: { width: raster.width, height: raster.height },
      pixels: toBase64(raster),
    });
  }

  function chooseCategory(next: string) {
    setCategory(next);
    // The polygon tool stamps its own category onto each closed polygon, so
    // it has to follow the picker or every polygon fills as the first class.
    setPolygonTool((current) => setPolygonCategory(current, next));
    if (tool === "erase") setTool("brush");
  }

  function closePolygon() {
    updatePolygons(closeDraft);
  }

  const resize = (delta: number) =>
    setRadius((current) => Math.min(MAX_RADIUS, Math.max(MIN_RADIUS, current + delta)));

  const hotkeys: Hotkey[] = [
    {
      keys: DIGIT_KEYS,
      display: "1–9",
      description: "选择类别",
      run: (key) => {
        const next = categories[Number(key) - 1];
        if (next) chooseCategory(next);
      },
    },
    { keys: ["h"], display: "H", description: "拖动画布", run: () => setTool("pan") },
    { keys: ["b"], display: "B", description: "画笔", run: () => setTool("brush") },
    { keys: ["e"], display: "E", description: "橡皮擦", run: () => setTool("erase") },
    { keys: ["p"], display: "P", description: "多边形", run: () => setTool("polygon") },
    { keys: ["["], display: "[", description: "缩小画笔", repeat: true, run: () => resize(-2) },
    { keys: ["]"], display: "]", description: "放大画笔", repeat: true, run: () => resize(2) },
    { keys: ["enter"], display: "Enter", description: "闭合当前多边形", run: closePolygon },
    {
      keys: ["backspace"],
      display: "Backspace",
      description: "撤销多边形最后一点",
      repeat: true,
      run: () => setPolygonTool((current) => undoLastPoint(current)),
    },
    {
      keys: ["escape"],
      display: "Esc",
      description: "放弃当前多边形",
      run: () => setPolygonTool((current) => cancelDraft(current)),
    },
    { keys: UNDO_KEYS, display: `${MOD_LABEL} + Z`, description: "撤销（绘制多边形时撤销一点）", run: undo },
    { keys: REDO_KEYS, display: `${MOD_LABEL} + Shift + Z`, description: "重做", run: redo },
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (!ready || !item) {
    return (
      <QueueFallback
        doneDescription="当前没有待分割图像，所有结果均已原子写入本地掩膜文件。"
        doneTitle="图像分割已完成"
        loading="正在读取分割队列…"
        projectId={projectId}
        queue={queue}
      />
    );
  }

  const draftPoints = polygonTool.draft?.length ?? 0;
  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!raster}
          error={submitError}
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hints={CANVAS_HINTS}
      hotkeys={hotkeys}
      history={{ canUndo: history.canUndo || draftPoints > 0, canRedo: history.canRedo, undo, redo }}
      panel={
        <PanelSection title="类别">
          <OptionList
            label="分割类别"
            name="segmentation-category"
            onToggle={chooseCategory}
            options={categories}
            selected={[category]}
            swatches
          />
          <p className="panel-note">掩膜每个像素只属于一个类别；后画的类别会覆盖先画的。</p>
        </PanelSection>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      remaining={ready.queue.total}
      stage={
        imageSize && imageUrl && raster ? (
          <ImageCanvas
            alt={imagePath ?? ""}
            imageSize={imageSize}
            interactionMode={tool === "pan" ? "pan" : "tool"}
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
      strip={<ImageStrip dirty={history.canUndo} projectId={projectId} queue={queue} />}
      title="图像分割"
      toolOptions={
        tool === "pan" ? undefined : tool === "polygon" ? (
          <p className="panel-note">
            {draftPoints === 0
              ? `逐点单击勾勒一个「${category}」区域，点回起点或按 Enter 闭合并填充。`
              : `已放置 ${draftPoints} 个点${draftPoints >= 3 ? "，按 Enter 闭合" : ""}；Esc 放弃。`}
          </p>
        ) : (
          <RangeField
            label={tool === "erase" ? "橡皮半径" : "画笔半径"}
            max={MAX_RADIUS}
            min={MIN_RADIUS}
            onChange={setRadius}
            unit="px"
            value={radius}
          />
        )
      }
      tools={[
        [{ id: "pan", label: "拖动画布", icon: <HandIcon />, shortcut: "H", active: tool === "pan", onSelect: () => setTool("pan") }],
        [
          { id: "brush", label: "画笔", icon: <BrushIcon />, shortcut: "B", active: tool === "brush", onSelect: () => setTool("brush") },
          { id: "erase", label: "橡皮擦", icon: <EraserIcon />, shortcut: "E", active: tool === "erase", onSelect: () => setTool("erase") },
          { id: "polygon", label: "多边形", icon: <PolygonIcon />, shortcut: "P", active: tool === "polygon", onSelect: () => setTool("polygon") },
        ],
      ]}
    />
  );
}

function drawPolygonOutline(
  context: CanvasRenderingContext2D,
  points: readonly { x: number; y: number }[],
  scale: number,
  color: string,
  isDraft: boolean,
) {
  if (points.length === 0) return;
  context.lineWidth = 2 / scale;
  context.strokeStyle = color;
  context.beginPath();
  context.moveTo(points[0].x, points[0].y);
  for (const point of points.slice(1)) context.lineTo(point.x, point.y);
  if (!isDraft) context.closePath();
  context.stroke();
  const radius = 3 / scale;
  for (const point of points) {
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    context.fillStyle = color;
    context.fill();
  }
}
