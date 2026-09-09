import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "wouter";

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
const PALETTE = ["#39c37a", "#ff8a3d", "#4f8dfd", "#e85d75", "#c99fff", "#f2c94c"];

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

function colorFor(index: number): readonly [number, number, number, number] {
  if (index === 0) return [0, 0, 0, 0];
  const [r, g, b] = hexToRgb(PALETTE[(index - 1) % PALETTE.length]);
  return [r, g, b, 140];
}

function hexToRgb(hex: string): [number, number, number] {
  const value = parseInt(hex.slice(1), 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

export function SegmentationReviewPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [mode, setMode] = useState<"brush" | "polygon">("brush");
  const [erasing, setErasing] = useState(false);
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
  const categories = state.kind === "ready" ? configuredCategories(state.project) : [];

  useEffect(() => {
    if (imageSize && !raster) {
      setRaster(createRasterBuffer(imageSize.width, imageSize.height, 0));
    }
  }, [imageSize, raster]);

  const activeIndex = erasing ? 0 : categoryIndex(categories, category);

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
          drawPolygonOutline(context, polygon.points, scale, false);
        }
        if (polygonTool.draft) {
          drawPolygonOutline(context, polygonTool.draft, scale, true);
        }
      },
    }),
    [raster, polygonTool],
  );

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!raster) return;
    if (mode === "polygon") {
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

  if (state.kind === "loading") return <LoadingState>正在读取分割队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <section className="state-panel review-complete">
        <p className="eyebrow">Queue complete</p>
        <h1>图像分割已完成</h1>
        <p>当前没有待分割图像，所有结果均已原子写入本地掩膜文件。</p>
        <Link className="text-link" to={`/projects/${projectId}`}>返回项目详情</Link>
      </section>
    );
  }

  return (
    <section className="classification-workspace">
      <div className="review-topbar">
        <div>
          <Link className="back-link" to={`/projects/${projectId}`}>← {state.project.name}</Link>
          <p className="eyebrow">Image segmentation</p>
          <h1>为这张图像标注分割掩膜</h1>
        </div>
        <div className="queue-count">
          <strong>{state.queue.total}</strong>
          <span>张待分割</span>
        </div>
      </div>

      <div className="classification-grid">
        <div className="segmentation-canvas">
          {imageSize && imageUrl && raster ? (
            <ImageCanvas
              alt={imagePath ?? ""}
              imageSize={imageSize}
              interactionMode="tool"
              layers={[layer]}
              onPointerEvent={handlePointer}
              onViewportChange={(next) => {
                viewportRef.current = next;
              }}
              shortcuts={[
                {
                  key: "enter",
                  description: "完成当前多边形",
                  onTrigger: () => {
                    setPolygonTool((current) => {
                      const next = closeDraft(current);
                      const closed = next.polygons.length > current.polygons.length;
                      if (closed) {
                        const finished = next.polygons[next.polygons.length - 1];
                        setRaster((buffer) => {
                          if (!buffer) return buffer;
                          fillPolygon(
                            buffer,
                            finished.points,
                            categoryIndex(categories, finished.category),
                          );
                          return { ...buffer };
                        });
                      }
                      return next;
                    });
                  },
                },
                {
                  key: "escape",
                  description: "取消当前多边形",
                  onTrigger: () => setPolygonTool((current) => cancelDraft(current)),
                },
              ]}
              src={imageUrl}
            />
          ) : (
            <LoadingState>正在读取图像尺寸…</LoadingState>
          )}
        </div>
        <div className="label-selector">
          <h2>分割类别</h2>
          <div role="group" aria-label="分割类别">
            {categories.map((option) => (
              <label className={option === category ? "selected" : ""} key={option}>
                <input
                  checked={option === category}
                  name="segmentation-category"
                  onChange={() => setCategory(option)}
                  type="radio"
                />
                <span>{option}</span>
              </label>
            ))}
          </div>
          <div role="group" aria-label="标注工具">
            <button aria-pressed={mode === "brush"} onClick={() => setMode("brush")} type="button">
              画笔
            </button>
            <button aria-pressed={mode === "polygon"} onClick={() => setMode("polygon")} type="button">
              多边形
            </button>
          </div>
          {mode === "brush" && (
            <>
              <label>
                画笔半径
                <input
                  max={100}
                  min={1}
                  onChange={(event) => setRadius(Number(event.target.value))}
                  type="range"
                  value={radius}
                />
              </label>
              <label>
                <input
                  checked={erasing}
                  onChange={(event) => setErasing(event.target.checked)}
                  type="checkbox"
                />
                擦除
              </label>
            </>
          )}
          <button
            className="classification-submit"
            disabled={submitting || !raster}
            onClick={() => void submit()}
            type="button"
          >
            {submitting ? "正在保存…" : "保存并继续"}
          </button>
          {submitError && (
            <div className="submit-error" role="alert">
              <span>保存失败：{submitError}</span>
              <button onClick={() => void submit()} type="button">重试保存</button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function drawPolygonOutline(
  context: CanvasRenderingContext2D,
  points: readonly { x: number; y: number }[],
  scale: number,
  isDraft: boolean,
) {
  if (points.length === 0) return;
  context.lineWidth = 2 / scale;
  context.strokeStyle = isDraft ? "#ff8a3d" : "#ffffff";
  context.beginPath();
  context.moveTo(points[0].x, points[0].y);
  for (const point of points.slice(1)) context.lineTo(point.x, point.y);
  if (!isDraft) context.closePath();
  context.stroke();
  const radius = 3 / scale;
  for (const point of points) {
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    context.fillStyle = isDraft ? "#ff8a3d" : "#ffffff";
    context.fill();
  }
}
