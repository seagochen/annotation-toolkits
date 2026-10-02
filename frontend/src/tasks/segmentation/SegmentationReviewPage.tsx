import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

import {
  getProject,
  getQueue,
  projectFileUrl,
  submitAnnotation,
  type ProjectDetail,
  type QueueResponse,
} from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import {
  CANVAS_HINTS,
  CompleteState,
  OptionList,
  PanelSection,
  RangeField,
  Segmented,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { categoryColor, hexToRgb } from "../../components/workspace/palette";
import { DIGIT_KEYS, MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import {
  ImageCanvas,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Viewport,
} from "../../components/image-canvas";
import {
  beginOrExtendDraft,
  cancelDraft,
  closeDraft,
  createPolygonToolState,
  setCategory as setPolygonCategory,
  undoLastPoint,
  type PolygonToolState,
} from "../../components/image-canvas/polygon-tool";
import {
  createRasterBuffer,
  fillPolygon,
  stampAt,
  strokeSegment,
  toBase64,
  toImageData,
  type RasterBuffer,
} from "../../components/image-canvas/raster-buffer";
import { useImageSize } from "../useImageSize";

type QueueItem = QueueResponse["items"][number];
type ReadyState = { project: ProjectDetail; queue: QueueResponse };
type PageState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyState)
  | { kind: "error"; message: string };

const HANDLE_SCREEN_SIZE = 8;
const MIN_RADIUS = 1;
const MAX_RADIUS = 100;

type Tool = "brush" | "erase" | "polygon";

function itemText(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

function configuredCategories(project: ProjectDetail): string[] {
  const value = project.summary.categories;
  return Array.isArray(value)
    ? value.filter((category): category is string => typeof category === "string")
    : [];
}

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
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [tool, setTool] = useState<Tool>("brush");
  const [radius, setRadius] = useState(12);
  const [category, setCategory] = useState("");
  const [raster, setRaster] = useState<RasterBuffer | null>(null);
  const [polygonTool, setPolygonTool] = useState<PolygonToolState>(createPolygonToolState());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });
  const painting = useRef(false);
  const lastPoint = useRef<{ x: number; y: number } | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitError("");
    try {
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId),
      ]);
      if (project.task_type !== "segmentation") {
        setState({ kind: "error", message: "该项目不是图像分割任务。" });
        return;
      }
      const categories = configuredCategories(project);
      setCategory(categories[0] ?? "");
      setPolygonTool(createPolygonToolState([], categories[0] ?? ""));
      setRaster(null);
      setState({ kind: "ready", project, queue });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "未知错误",
      });
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const item = state.kind === "ready" ? state.queue.items[0] : undefined;
  const imagePath = item ? itemText(item, "image_path") : undefined;
  const imageUrl = imagePath ? projectFileUrl(projectId, imagePath) : undefined;
  const imageSize = useImageSize(imageUrl);
  const categories = useMemo(
    () => (state.kind === "ready" ? configuredCategories(state.project) : []),
    [state],
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
        const offscreen = document.createElement("canvas");
        offscreen.width = raster.width;
        offscreen.height = raster.height;
        const offscreenContext = offscreen.getContext("2d");
        if (!offscreenContext) return;
        offscreenContext.putImageData(toImageData(raster, colorFor), 0, 0);
        context.drawImage(offscreen, 0, 0);

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

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!raster) return;
    if (tool === "polygon") {
      if (event.phase === "down") {
        const closeDistance = HANDLE_SCREEN_SIZE / viewportRef.current.scale;
        setPolygonTool((current) => {
          const next = beginOrExtendDraft(current, event.image, closeDistance);
          const closed = next.polygons.length > current.polygons.length;
          if (closed) {
            const finished = next.polygons[next.polygons.length - 1];
            setRaster((buffer) => {
              if (!buffer) return buffer;
              fillPolygon(buffer, finished.points, categoryIndex(categories, finished.category));
              return { ...buffer };
            });
          }
          return next;
        });
      }
      return;
    }
    if (event.phase === "down") {
      painting.current = true;
      lastPoint.current = event.image;
      setRaster((buffer) => {
        if (!buffer) return buffer;
        stampAt(buffer, event.image, radius, () => activeIndex);
        return { ...buffer };
      });
    } else if (event.phase === "move" && painting.current && lastPoint.current) {
      const from = lastPoint.current;
      lastPoint.current = event.image;
      setRaster((buffer) => {
        if (!buffer) return buffer;
        strokeSegment(buffer, from, event.image, radius, () => activeIndex);
        return { ...buffer };
      });
    } else if (event.phase === "up" || event.phase === "cancel") {
      painting.current = false;
      lastPoint.current = null;
    }
  }

  async function submit() {
    if (!item || !raster || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      await submitAnnotation(projectId, itemText(item, "item_id"), {
        image_size: { width: raster.width, height: raster.height },
        pixels: toBase64(raster),
      });
      const queue = await getQueue(projectId);
      setState((current) =>
        current.kind === "ready" ? { ...current, queue } : current,
      );
      setRaster(null);
      setPolygonTool(createPolygonToolState([], category));
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "未知错误");
    } finally {
      setSubmitting(false);
    }
  }

  function chooseCategory(next: string) {
    setCategory(next);
    // The polygon tool stamps its own category onto each closed polygon, so
    // it has to follow the picker or every polygon fills as the first class.
    setPolygonTool((current) => setPolygonCategory(current, next));
    if (tool === "erase") setTool("brush");
  }

  function closePolygon() {
    setPolygonTool((current) => {
      const next = closeDraft(current);
      const closed = next.polygons.length > current.polygons.length;
      if (closed) {
        const finished = next.polygons[next.polygons.length - 1];
        setRaster((buffer) => {
          if (!buffer) return buffer;
          fillPolygon(buffer, finished.points, categoryIndex(categories, finished.category));
          return { ...buffer };
        });
      }
      return next;
    });
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
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (state.kind === "loading") return <LoadingState>正在读取分割队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <CompleteState
        description="当前没有待分割图像，所有结果均已原子写入本地掩膜文件。"
        projectId={projectId}
        title="图像分割已完成"
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
      panel={
        <>
          <PanelSection title="类别">
            <OptionList
              label="分割类别"
              name="segmentation-category"
              onToggle={chooseCategory}
              options={categories}
              selected={[category]}
              swatches
            />
          </PanelSection>
          <PanelSection title="工具">
            <Segmented
              label="标注工具"
              onChange={setTool}
              options={[
                { value: "brush", label: "画笔", key: "B" },
                { value: "erase", label: "橡皮", key: "E" },
                { value: "polygon", label: "多边形", key: "P" },
              ]}
              value={tool}
            />
            {tool === "polygon" ? (
              <p className="panel-note">
                {draftPoints === 0
                  ? "逐点单击勾勒区域，点回起点或按 Enter 闭合并填充。"
                  : `已放置 ${draftPoints} 个点${draftPoints >= 3 ? "，按 Enter 闭合" : ""}；Esc 放弃。`}
              </p>
            ) : (
              <RangeField
                label="画笔半径"
                max={MAX_RADIUS}
                min={MIN_RADIUS}
                onChange={setRadius}
                unit="px"
                value={radius}
              />
            )}
          </PanelSection>
        </>
      }
      progress={summaryProgress(state.project.summary, state.queue.total)}
      projectId={projectId}
      projectName={state.project.name}
      remaining={state.queue.total}
      stage={
        imageSize && imageUrl && raster ? (
          <ImageCanvas
            alt={imagePath ?? ""}
            imageSize={imageSize}
            interactionMode="tool"
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
      title="图像分割"
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
